"""
Les pages d'un document, préparées pour les IA qui lisent des images (GPT, Mistral « vision »)
ou des PDF d'une page (Claude). Même principe que pour Gemini : chaque page garde son numéro
dans le livre (« === PAGE n === »), pour que l'IA renvoie les bons numéros de page.
"""
import io
import threading
from typing import Optional

from .base import ProviderError, SourceDocument

# pdfium (la bibliothèque qui transforme les pages en images) ne supporte PAS deux appels en
# même temps : sur un serveur qui traite plusieurs requêtes, les conversions échouaient en
# silence, ou le service plantait. Un seul appel à la fois, et chaque page / image / document
# est fermé tout de suite, à l'intérieur du verrou (sinon Python le libère plus tard, n'importe quand).
PDFIUM_LOCK = threading.Lock()

# Au-delà, une seule requête devient trop lourde pour GPT / Claude / Mistral :
# le PLAN du livre entier (étape 1) se fait avec Gemini ; les leçons (2 à 4 pages) passent partout.
MAX_PAGES_PER_REQUEST = 100


def render_pdf_pages(data: bytes, dpi: int, quality: int, max_pages: Optional[int] = None) -> list[bytes]:
    """Chaque page d'un PDF en image JPEG. Sûr quand plusieurs requêtes arrivent en même temps.

    ImportError si pypdfium2 manque ; ValueError si le PDF a plus de max_pages pages ;
    toute autre exception si le PDF est illisible.
    """
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            if max_pages is not None and len(pdf) > max_pages:
                raise ValueError(len(pdf))
            images = []
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    bitmap = page.render(scale=dpi / 72)
                    try:
                        out = io.BytesIO()
                        bitmap.to_pil().convert("RGB").save(out, "JPEG", quality=quality)
                        images.append(out.getvalue())
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            return images
        finally:
            pdf.close()


def page_images(doc: SourceDocument, dpi: int = 150, quality: int = 80) -> list[bytes]:
    """Chaque page du document en image JPEG (150 dpi : assez net pour compter de petits objets)."""
    try:
        return render_pdf_pages(doc.data, dpi, quality, max_pages=MAX_PAGES_PER_REQUEST)
    except ImportError as exc:  # pragma: no cover
        raise ProviderError("Installer le paquet : pip install pypdfium2 pillow") from exc
    except ValueError as exc:
        raise ProviderError(
            f"{exc.args[0]} pages en une requête : trop pour ce fournisseur. "
            f"Faire le plan du livre (étape 1) avec Gemini, puis les leçons avec ce fournisseur."
        ) from exc
    except Exception as exc:
        raise ProviderError(f"PDF illisible pour la conversion en images : {exc}") from exc


def page_pdfs(doc: SourceDocument) -> list[bytes]:
    """Chaque page du document en PDF d'une page (Claude lit les PDF : texte + image de la page)."""
    if doc.pages and doc.pages_mime == "application/pdf":
        pages = list(doc.pages)
    else:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(io.BytesIO(doc.data))
        pages = []
        for page in reader.pages:
            writer = PdfWriter()
            writer.add_page(page)
            out = io.BytesIO()
            writer.write(out)
            pages.append(out.getvalue())
    if len(pages) > MAX_PAGES_PER_REQUEST:
        raise ProviderError(
            f"{len(pages)} pages en une requête : trop pour ce fournisseur. "
            f"Faire le plan du livre (étape 1) avec Gemini, puis les leçons avec ce fournisseur."
        )
    return pages
