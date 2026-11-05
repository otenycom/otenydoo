import { _t } from "@web/core/l10n/translation";
import { Dialog } from "@web/core/dialog/dialog";
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";
import { hidePDFJSButtons } from "@web/core/utils/pdfjs";
import { Component, useEffect, useRef } from "@odoo/owl";

/**
 * Shows a PDF attachment in a dialog, with Print, Download and Close in the
 * footer (Ries, 5-Nov-2026). A new window is blocked as a popup, and a plain
 * download makes the user open the file again to print it.
 *
 * Only the PDF viewer scrolls: the dialog has a fixed height and its body does
 * not scroll. The viewer opens at the zoom "page-width", so a page never
 * scrolls sideways. The Print and Download buttons of the viewer are hidden,
 * because the footer has them.
 */
export class PdfPreviewDialog extends Component {
    static template = "riverflow.PdfPreviewDialog";
    static components = { Dialog };
    static props = {
        attachmentId: Number,
        title: String,
        close: Function,
    };

    setup() {
        this.iframeRef = useRef("iframe");
        useEffect(
            (iframe) => {
                if (iframe) {
                    hidePDFJSButtons(iframe, {
                        hideDownload: true,
                        hidePrint: true,
                        hidePresentation: true,
                        hideRotation: true,
                    });
                    // A click in the PDF moves the keyboard focus into the
                    // frame, where Odoo's Escape hotkey does not hear it.
                    iframe.addEventListener("load", () => {
                        iframe.contentWindow.addEventListener("keydown", (event) => {
                            if (event.key === "Escape") {
                                this.props.close();
                            }
                        });
                    });
                }
            },
            () => [this.iframeRef.el]
        );
    }

    get fileUrl() {
        return `/web/content/${this.props.attachmentId}`;
    }

    get viewerUrl() {
        const file = encodeURIComponent(this.fileUrl);
        return `/web/static/lib/pdfjs/web/viewer.html?file=${file}#zoom=page-width`;
    }

    onPrint() {
        const viewer = this.iframeRef.el?.contentWindow;
        if (viewer?.PDFViewerApplication) {
            viewer.PDFViewerApplication.triggerPrinting();
        } else {
            viewer?.print();
        }
    }

    async onDownload() {
        await download({ url: `${this.fileUrl}?download=true`, data: {} });
    }
}

/** Client action "riverflow_pdf_preview" (riverflow.util.pdf_preview_action):
 * closes the dialog that asked for it, if any, and opens the preview of the
 * attachment in params.attachment_id. */
function openPdfPreview(env, action) {
    env.services.dialog.add(PdfPreviewDialog, {
        attachmentId: action.params.attachment_id,
        title: action.params.title || _t("Document"),
    });
    return { type: "ir.actions.act_window_close" };
}

registry.category("actions").add("riverflow_pdf_preview", openPdfPreview);
