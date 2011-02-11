#===============================================================================
# Copyright 2011 Matt Chaput
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#===============================================================================

from array import array
from heapq import nlargest, nsmallest

from whoosh.searching import Results
from whoosh.util import now


class Sorter(object):
    """This object does the work of sorting search results.
    
    For simple sorting (where all fields go in the same direction), you can
    just use the ``sortedby`` and ``reverse`` arguments to
    :meth:`whoosh.searching.Searcher.search`::
    
        # Sort by ascending group
        r = searcher.search(myquery, sortedby="group")
        # Sort by ascending path and the ascending price price
        r = searcher.search(myquery, sortedby=("path", "price"))
        # Sort by descending path
        r = searcher.search(myquery, sortedby="path", reverse=True)
    
    These are the equivalent of using the sorter directly::
    
        # Sort by ascending path and the ascending price price
        sorter = searcher.sorter()
        sorter.add_field("path")
        sorter.add_field("price")
        r = sorter.sort_query(myquery)
    
    For complex sorting (where some fields are ascending and some fields are
    descending), you must instantiate a sorter object from the searcher and
    specify the fields to sort by::
    
        # Sort by ascending group and then descending price
        sorter = searcher.sorter()
        sorter.add_field("group")
        sorter.add_field("price", reverse=True)
        r = sorter.sort_query(myquery)
    
    Alternatively, you can set up the sort criteria using a keyword argument::
    
        # Sort by ascending group and then descending price
        crits = [("group", False), ("price", True)]
        sorter = searcher.sorter(criteria=crits)
        r = sorter.sort_query(myquery)
    
    Note that complex sorting can be much slower on large indexes than a
    sort in which all fields are sorted in the same direction. Also, when you
    do this type of sort on a multi-segment index, the sort cannot reuse field
    caches and must recreate a field cache-like structure across the entire
    index, which can effectively double memory usage for cached fields.
    
    You can re-use a configured sorter with different queries. However, the
    sorter object always returns results from the searcher it was created with.
    If the index changes and you refresh the searcher, you need to recreate the
    sorter object to see the updates.
    """

    def __init__(self, searcher, criteria=None, sortedby=None):
        """
        :param searcher: a :class:`whoosh.searching.Searcher` object to use for
            searching.
        :param criteria: a list of ``(fieldname, reversed)`` tuples, where the
            second value in each tuple is a boolean indicating whether to
            reverse the order of the sort for that field. Alternatively you can
            use the :meth:`Sorter.add_field` method on the instantiated sorter.
        :param sortedby: a convenience that generates a proper "criteria" list
            from a fieldname string or list of fieldnames, to set up the sorter
            for a simple search.
        """
        
        self.searcher = searcher
        self.criteria = criteria or []
        if sortedby:
            if isinstance(sortedby, basestring):
                sortedby = [sortedby]
            for fieldname in sortedby:
                self.criteria.append((fieldname, False))
        
        self.arrays = None

    def add_field(self, fieldname, reverse=False):
        self.criteria.append(fieldname, reverse)
    
    def is_simple(self):
        if len(self.criteria) < 2:
            return True
        
        firstdir = self.criteria[0][1]
        return all(c[1] == firstdir for c in self.criteria)
    
    def _results(self, q, docnums, docset, runtime):
        top_n = [(None, docnum) for docnum in docnums]
        return Results(self.searcher, q, top_n, docset, runtime=runtime)
    
    def _simple_sort_query(self, q, limit=None, reverse=False, filter=None):
        # If the direction of all sort fields is the same, we can use field
        # caches to do the sorting
        
        t = now()
        docset = set()
        sortedby = [c[0] for c in self.criteria]
        reverse = self.criteria[0][1] ^ reverse
        comb = self.searcher._filter_to_comb(filter)
        
        if self.searcher.subsearchers:
            heap = []
            
            # I wish I could actually do a heap thing here, but the Python heap
            # queue only works with greater-than, and I haven't thought of a
            # smart way to get around that yet, so I'm being dumb and using
            # nlargest/nsmallest on the heap + each subreader list :(
            op = nlargest if reverse else nsmallest
            
            for s, offset in self.searcher.subsearchers:
                # This searcher is wrapping a MultiReader, so push the sorting
                # down to the leaf readers and then combine the results.
                docnums = [docnum for docnum in q.docs(s)
                           if (not comb) or docnum + offset in comb]
                
                # Add the docnums to the docset
                docset.update(docnums)
                
                # Ask the reader to return a list of (key, docnum) pairs to
                # sort by. If limit=None, the returned list is not sorted. If
                # limit=True, it is sorted.
                r = s.reader()
                srt = r.key_docs_by(sortedby, docnums, limit, reverse=reverse,
                                    offset=offset)
                if limit:
                    # Pick the "limit" smallest/largest items from the current
                    # and new list
                    heap = op(limit, heap + srt)
                else:
                    # If limit=None, we'll just add everything to the "heap"
                    # and sort it at the end.
                    heap.extend(srt)
            
            # Sort the heap and take the docnums
            docnums = [docnum for _, docnum in sorted(heap, reverse=reverse)]
            
        else:
            # This searcher is wrapping an atomic reader, so we don't need to
            # get tricky combining the results of multiple readers, just ask
            # the reader to sort the results.
            r = self.searcher.reader()
            docnums = [docnum for docnum in q.docs(self.searcher)
                       if (not comb) or docnum in comb]
            docnums = r.sort_docs_by(sortedby, docnums, reverse=reverse)
            docset = set(docnums)
            
            # I artificially enforce the limit here, even thought the current
            # implementation can't use it, so that the results don't change
            # based on single- vs- multi-segment.
            docnums = docnums[:limit]
        
        runtime = now() - t
        return self._results(q, docnums, docset, runtime)
    
    def _complex_cache(self):
        self.arrays = []
        r = self.searcher.reader()
        for name, reverse in self.criteria:
            if r.is_atomic() and r.supports_caches():
                # If this is an atomic file reader, we can re-use the field
                # cache's order
                order = r.fieldcache().order
                if reverse:
                    order = order[:]
                    order.reverse()
                self.arrays.append(order)
            else:
                arry = array("i", [0] * r.doc_count_all())
                field = self.searcher.schema[name]
                for i, (t, _) in enumerate(field.sortable_values(r, name)):
                    if reverse:
                        i = 0 - i
                    postings = r.postings(name, t)
                    for docid in postings.all_ids():
                        arry[docid] = i
                self.arrays.append(arry)

    def _complex_key_fn(self, docnum):
        return tuple(arry[docnum] for arry in self.arrays)

    def _complex_sort_query(self, q, limit=None, reverse=False, filter=None):
        # If 
        
        t = now()
        if self.arrays is None:
            self._complex_cache()
        comb = self.searcher._filter_to_comb(filter)
        docnums = [docnum for docnum in self.searcher.docs_for_query(q)
                   if (not comb) or docnum in comb]
        docnums.sort(key=self._complex_key_fn, reverse=reverse)
        docset = set(docnums)
        
        # I artificially enforce the limit here, even thought the current
        # implementation can't use it, so that the results don't change based
        # on single- vs- multi-segment.
        if limit:
            docnums = docnums[:limit]
        runtime = now() - t
        return self._results(q, docnums, docset, runtime)

    def sort_query(self, q, limit=None, reverse=False, filter=None):
        if self.is_simple():
            meth = self._simple_sort_query
        else:
            meth = self._complex_sort_query
            
        return meth(q, limit, reverse, filter)
    



