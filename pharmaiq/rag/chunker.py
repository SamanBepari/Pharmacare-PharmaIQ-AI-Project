"""Split extracted blocks into overlapping, citation-friendly chunks."""
import re


def chunk_blocks(blocks, chunk_words=140, overlap=30):
    """Chunks never cross a page or section boundary, so every citation is precise."""
    chunks = []
    for b in blocks:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", b["text"]) if p.strip()]
        if len(paragraphs) == 1:      # no blank lines → split by lines
            paragraphs = [p.strip() for p in b["text"].splitlines() if p.strip()]
        words_buf = []

        def emit():
            if words_buf:
                chunks.append({"page": b["page"], "section": b["section"], "text": " ".join(words_buf)})

        for para in paragraphs:
            pw = para.split()
            if len(pw) > chunk_words:                     # very long paragraph: window it
                emit()
                words_buf = []
                step = max(1, chunk_words - overlap)
                for i in range(0, len(pw), step):
                    chunks.append({"page": b["page"], "section": b["section"],
                                   "text": " ".join(pw[i:i + chunk_words])})
                    if i + chunk_words >= len(pw):
                        break
                continue
            if len(words_buf) + len(pw) > chunk_words and words_buf:
                emit()
                words_buf = words_buf[-overlap:] if overlap else []
            words_buf.extend(pw)
        emit()
    return chunks
