import io
import json

from pypdf import PdfWriter

from app.cli import main


def make_pdf(path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())


def test_cli_with_fake_provider(tmp_path, capsys):
    pdf = tmp_path / "unite1.pdf"
    make_pdf(pdf, 3)
    out = tmp_path / "res.json"
    code = main([str(pdf), "--provider", "fake", "--language", "ar", "--out", str(out)])
    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["draft"]["source_file"] == "unite1.pdf"
    assert "Skills : 4" in capsys.readouterr().out


def test_cli_missing_file():
    assert main(["introuvable.pdf", "--provider", "fake"]) == 1


def test_cli_refuses_whole_book_without_pages(tmp_path, capsys):
    book = tmp_path / "livre.pdf"
    make_pdf(book, 60)
    assert main([str(book), "--provider", "fake"]) == 1
    assert "--pages" in capsys.readouterr().out


def test_cli_keeps_selected_pages(tmp_path, capsys):
    book = tmp_path / "livre.pdf"
    make_pdf(book, 60)
    out = tmp_path / "res.json"
    assert main([str(book), "--provider", "fake", "--pages", "5-24", "--out", str(out)]) == 0
    assert "Pages gardées : 5-24 (20 sur 60)" in capsys.readouterr().out
