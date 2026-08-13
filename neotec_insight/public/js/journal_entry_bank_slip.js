// Neotec Insight — embed the bank-slip reader inside Journal Entry.
// Adds a "Read Bank Slip" button: upload a PDF/image and auto-fill the journal
// header (posting date, cheque/reference no, remark) from the extracted data.
// The accounts table is left for the user to complete and submit.

frappe.ui.form.on('Journal Entry', {
  refresh(frm) {
    frm.add_custom_button(__('Read Bank Slip'), () => nbi_read_slip_je(frm), __('Bank'));
  },
});

function nbi_read_slip_je(frm) {
  new frappe.ui.FileUploader({
    doctype: frm.doctype,
    docname: frm.doc.name,
    allow_multiple: false,
    restrictions: { allowed_file_types: ['.pdf', '.png', '.jpg', '.jpeg', '.webp'] },
    on_success(file_doc) {
      frappe.call({
        method: 'neotec_insight.neotec_insight.api.bank_reader.read_slip_into',
        args: { file_url: file_doc.file_url, target_doctype: 'Journal Entry', company: frm.doc.company },
        freeze: true,
        freeze_message: __('Reading slip…'),
        callback: ({ message }) => {
          if (!message) return;
          const fm = message.field_map || {};
          const total = fm.total_amount_hint;
          delete fm.total_amount_hint;
          Object.keys(fm).forEach((k) => frm.set_value(k, fm[k]));
          let note = __('Filled header from slip ({0}, {1}% conf.)', [message.method, Math.round((message.confidence || 0) * 100)]);
          if (total) note += ' · ' + __('Amount on slip: {0}', [format_currency(total)]);
          if (message.counterparty_name) note += ' · ' + message.counterparty_name;
          frappe.show_alert({ message: note, indicator: 'green' }, 7);
        },
      });
    },
  });
}
