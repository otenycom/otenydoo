# PDF preview dialog (`riverflow_pdf_preview`)

*Developer reference. Built on 5-Nov-2026 (Ries) for the PoA - DE Permit Renewal of CrewRadar.*

## Rule

To show a PDF to the user, return `pdf_preview_action(attachment)` from `riverflow.util`.
Never return an act_url with target "new": the browser blocks a window that opens after a
server answer as a popup, and a plain download makes the user open the file again to print it.

## What the user sees

The client action `riverflow_pdf_preview` closes the dialog that returned it, if any (a
transition wizard, for example), and opens a dialog with Odoo's own pdf.js viewer
(`/web/static/lib/pdfjs/web/viewer.html`). The footer has **Print**, **Download** and
**Close**. The viewer's own Print and Download buttons are hidden, so each action is on the
screen once. The dialog title is the attachment name.

- Print calls `PDFViewerApplication.triggerPrinting()` inside the frame, so the browser's print
  dialog opens with no new window.
- Download uses `download()` of `@web/core/network/download` (the same tab).
- Escape closes the dialog, also after a click in the PDF: the frame passes the key to the
  dialog, because Odoo's Escape hotkey does not hear a key pressed inside the frame.

## One scroll layer

Only the viewer scrolls. The dialog has the window height and its body does not scroll; the
body rule matches the specificity of the full-screen rule of `web/core/dialog/dialog.scss`, so
it wins on a phone as well. The viewer opens at the zoom `page-width`, so a page never scrolls
sideways.

## Limits

- pdf.js shows only PDFs. A photo needs another way (a caller checks the mimetype).
- On an iPhone or iPad, printing from the frame is unreliable; Download is the path there.
- The attachment stays as the caller made it; a temporary attachment is not cleaned up.

## Files

- `riverflow/util.py` — `pdf_preview_action(attachment)`.
- `riverflow/static/src/components/pdf_preview/` — the dialog (JS, QWeb, SCSS).
