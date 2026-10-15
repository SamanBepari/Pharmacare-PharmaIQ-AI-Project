import unittest
from tests.helpers import build_db
from pharmaiq import config
from pharmaiq.rag import chunker, ingest, store
from pharmaiq.rag.retriever import Retriever
from pharmaiq.rag.text import stem, tokens


class RAGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db()
        cls.settings = config.get_settings()
        cls.r = Retriever(cls.db, "admin", cls.settings)

    def top(self, q):
        hits = [h for h in self.r.search(q, 3) if h.relevant]
        return hits[0].title if hits else None

    def test_stemmer_matches_word_forms(self):
        self.assertEqual(stem("stored"), stem("storing"))
        self.assertEqual(stem("stores"), stem("store"))

    def test_synonym_fridge(self):
        self.assertEqual(tokens("fridge"), tokens("refrigerator"))

    def test_finds_right_document(self):
        self.assertIn("Insulin", self.top("how should insulin be stored"))
        self.assertIn("Returns", self.top("what is the return policy"))
        self.assertIn("Metformin", self.top("side effects of metformin"))

    def test_typo_tolerance(self):
        self.assertIn("Paracetamol", self.top("paracetmol overdose liver"))

    def test_named_medicine_focus(self):
        hits = self.r.search("side effects of metformin", 4)
        self.assertTrue(all("Metformin" in h.title for h in hits if h.relevant))

    def test_unrelated_question_has_no_relevant_hits(self):
        for q in ("who won the football world cup", "capital of france", "explain quantum computing"):
            self.assertFalse(any(h.relevant for h in self.r.search(q, 4)), q)

    def test_role_based_document_visibility(self):
        store.add_document(self.db, "secret.md", b"# Board Memo\n\nThe acquisition codename is BLUEHERON.\n", doc_type="reference", min_role="admin")
        self.assertTrue(any(h.relevant for h in Retriever(self.db, "admin", self.settings).search("acquisition codename blueheron", 3)))
        for role in ("staff", "pharmacist"):
            self.assertFalse(any(h.relevant for h in Retriever(self.db, role, self.settings).search("acquisition codename blueheron", 3)))

    def test_duplicate_upload_detected(self):
        data = b"# Dup\n\nsome unique text for dedupe testing."
        a = store.add_document(self.db, "d.md", data)
        b = store.add_document(self.db, "d2.md", data)
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])

    def test_chunker_keeps_pages_separate(self):
        blocks = [{"page": 1, "section": "", "text": "alpha " * 50}, {"page": 2, "section": "", "text": "beta " * 50}]
        chunks = chunker.chunk_blocks(blocks, 140, 30)
        self.assertEqual({c["page"] for c in chunks}, {1, 2})
        for c in chunks:
            self.assertFalse("alpha" in c["text"] and "beta" in c["text"])

    def test_ingest_rejects_bad_files(self):
        with self.assertRaises(ingest.IngestError):
            ingest.extract_blocks("x.exe", b"abc")
        with self.assertRaises(ingest.IngestError):
            ingest.extract_blocks("x.txt", b"")

    def test_docx_ingest(self):
        import io, docx
        d = docx.Document()
        d.add_heading("Cold Room SOP", 1)
        d.add_paragraph("Vaccines are kept at two to eight degrees.")
        buf = io.BytesIO()
        d.save(buf)
        blocks = ingest.extract_blocks("cold.docx", buf.getvalue())
        self.assertEqual(blocks[0]["section"], "Cold Room SOP")
        self.assertIn("Vaccines", blocks[0]["text"])


if __name__ == "__main__":
    unittest.main()
