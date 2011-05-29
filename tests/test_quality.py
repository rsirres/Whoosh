from __future__ import with_statement
import random

from nose.tools import assert_equal, assert_almost_equal, assert_not_equal

from whoosh import analysis, fields, formats
from whoosh.filedb.filepostings import FilePostingWriter, FilePostingReader
from whoosh.filedb.filestore import RamStorage
from whoosh.filedb.filetables import TermInfo
from whoosh.filedb.postblocks import current
from whoosh.util import length_to_byte, byte_to_length


def test_block():
    st = RamStorage()
    f = st.create_file("postfile")
    
    b = current(f, 0)
    b.append(0, 1.0, '', 1)
    b.append(1, 2.0, '', 2)
    b.append(2, 12.0, '', 6)
    b.append(5, 6.5, '', 420)
    assert b
    
    assert_equal(len(b), 4)
    assert_equal(list(b.ids), [0, 1, 2, 5])
    assert_equal(list(b.weights), [1.0, 2.0, 12.0, 6.5])
    assert_equal(b.values, None)
    assert_equal(b.min_length(), 1)
    assert_equal(b.max_length(), byte_to_length(length_to_byte(420)))
    assert_equal(b.max_weight(), 12.0)
    assert_equal(b.max_wol(), 2.0)
    
    ti = TermInfo()
    ti.add_block(b)
    assert_equal(ti.frequency(), 21.5)
    assert_equal(ti.doc_frequency(), 4)
    assert_equal(ti.min_length(), 1)
    assert_equal(ti.max_length(), byte_to_length(length_to_byte(420)))
    assert_equal(ti.max_weight(), 12.0)
    assert_equal(ti.max_wol(), 2.0)
    
    b.write(compression=3)
    f.close()
    f = st.open_file("postfile")
    bb = current.from_file(f, 0)
    
    bb.read_ids()
    assert_equal(list(bb.ids), [0, 1, 2, 5])
    bb.read_weights()
    assert_equal(list(bb.weights), [1.0, 2.0, 12.0, 6.5])
    bb.read_values()
    assert_equal(b.values, None)
    assert_equal(bb.min_length(), 1)
    assert_equal(bb.max_length(), byte_to_length(length_to_byte(420)))
    assert_equal(bb.max_weight(), 12.0)
    assert_equal(bb.max_wol(), 2.0)

def test_lowlevel_block_writing():
    st = RamStorage()
    f = st.create_file("postfile")
    fpw = FilePostingWriter(f, blocklimit=4)
    ana = analysis.StandardAnalyzer()
    fmt = formats.Frequency(ana)
    fpw.start(fmt)
    fpw.write(0, 1.0, fmt.encode(1.0), 1)
    fpw.write(1, 2.0, fmt.encode(2.0), 2)
    fpw.write(2, 12.0, fmt.encode(12.0), 6)
    fpw.write(5, 6.5, fmt.encode(6.5), 420)
    
    fpw.write(11, 1.5, fmt.encode(1.5), 1)
    fpw.write(12, 2.5, fmt.encode(2.5), 2)
    fpw.write(26, 100.5, fmt.encode(100.5), 21)
    fpw.write(50, 8.0, fmt.encode(8.0), 1020)
    ti = fpw.finish()
    
    assert_equal(ti.frequency(), 134.0)
    assert_equal(ti.doc_frequency(), 8)
    assert_equal(ti.min_length(), 1)
    assert_equal(ti.max_length(), byte_to_length(length_to_byte(1020)))
    assert_equal(ti.max_weight(), 100.5)
    assert_equal(ti.max_wol(), 100.5 / byte_to_length(length_to_byte(21)))

def test_midlevel_writing():
    st = RamStorage()
    schema = fields.Schema(t=fields.TEXT(phrase=False))
    ix = st.create_index(schema)
    w = ix.writer()
    w.add_document(t=u"alfa bravo charlie delta alfa bravo alfa")
    w.commit()
    
    with ix.reader() as r:
        ti = r.termsindex["t", u"alfa"]
        assert_equal(ti.frequency(), 3.0)
        assert_equal(ti.doc_frequency(), 1)
        assert_equal(ti.min_length(), 7)
        assert_equal(ti.max_length(), 7)
        assert_equal(ti.max_weight(), 3.0)
        assert_almost_equal(ti.max_wol(), 3.0 / 7)
        assert_equal(ti.postings, ((0, ), (3.0, ), ('\x00\x00\x00\x03', )))

    w = ix.writer()
    w.add_document(t=u"alfa charlie alfa")
    w.commit()
    
    with ix.reader() as r:
        ti = r.termsindex["t", u"alfa"]
        assert_equal(ti.frequency(), 5.0)
        assert_equal(ti.doc_frequency(), 2)
        assert_equal(ti.min_length(), 3)
        assert_equal(ti.max_length(), 7)
        assert_equal(ti.max_weight(), 3.0)
        assert_almost_equal(ti.max_wol(), 2.0 / 3)
        assert_equal(ti.postings, 0)

def _discreet(length):
    return byte_to_length(length_to_byte(length))

def test_max_field_length():
    st = RamStorage()
    schema = fields.Schema(t=fields.TEXT)
    ix = st.create_index(schema)
    for i in xrange(1, 200, 7):
        w = ix.writer()
        w.add_document(t=u" ".join(["word"] * i))
        w.commit()
        
        with ix.reader() as r:
            assert_equal(r.max_field_length("t"), _discreet(i))
    
def test_minmax_field_length():
    st = RamStorage()
    schema = fields.Schema(t=fields.TEXT)
    ix = st.create_index(schema)
    least = 999999
    most = 0
    for _ in xrange(1, 200, 7):
        w = ix.writer()
        count = random.randint(1, 100)
        least = min(count, least)
        most = max(count, most)
        w.add_document(t=u" ".join(["word"] * count))
        w.commit()
        
        with ix.reader() as r:
            assert_equal(r.min_field_length("t"), _discreet(least))
            assert_equal(r.max_field_length("t"), _discreet(most))

def test_reader_methods():
    st = RamStorage()
    schema = fields.Schema(t=fields.TEXT)
    ix = st.create_index(schema)
    w = ix.writer()
    w.add_document(t=u"alfa bravo charlie delta echo")
    w.add_document(t=u"bravo charlie delta echo foxtrot")
    w.add_document(t=u"charlie delta echo foxtrot golf")
    w.add_document(t=u"delta echo foxtrot")
    w.add_document(t=u"echo foxtrot golf hotel india juliet")
    w.add_document(t=u"foxtrot alfa alfa alfa")
    w.commit()

    with ix.reader() as r:
        assert_equal(r.frequency("t", u"alfa"), 4.0)
        assert_equal(r.doc_frequency("t", u"alfa"), 2)
        assert_equal(r.min_length("t", u"alfa"), 4)
        assert_equal(r.min_length("t", u"echo"), 3)
        assert_equal(r.max_length("t", u"alfa"), 5)
        assert_equal(r.max_weight("t", u"alfa"), 3.0)
        assert_equal(r.max_wol("t", u"alfa"), 3.0 / 4.0)
        assert_equal(r.doc_field_length(3, "t"), 3)
        assert_equal(r.min_field_length("t"), 3)
        assert_equal(r.max_field_length("t"), 6)
        
    w = ix.writer()
    w.add_document(t=u"alfa")
    w.add_document(t=u"bravo charlie")
    w.add_document(t=u"echo foxtrot tango bravo")
    w.add_document(t=u"golf hotel")
    w.add_document(t=u"india")
    w.add_document(t=u"juliet alfa bravo charlie delta echo foxtrot")
    w.commit(merge=False)

    with ix.reader() as r:
        assert_equal(r.frequency("t", u"alfa"), 6.0)
        assert_equal(r.doc_frequency("t", u"alfa"), 4)
        assert_equal(r.min_length("t", u"alfa"), 1)
        assert_equal(r.min_length("t", u"echo"), 3)
        assert_equal(r.max_length("t", u"alfa"), 7)
        assert_equal(r.max_weight("t", u"alfa"), 3.0)
        assert_equal(r.max_wol("t", u"alfa"), 1.0)
        assert_equal(r.min_field_length("t"), 1)
        assert_equal(r.max_field_length("t"), 7)


