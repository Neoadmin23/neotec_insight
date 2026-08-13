// Neotec Insight — embed the bank-slip reader inside Payment Entry.
// Adds a "Read Bank Slip" button: upload a PDF/image, auto-fill the document
// from the extracted data, then pick the party from a rich dropdown.

frappe.ui.form.on('Payment Entry', {
  refresh(frm) {
    frm.add_custom_button(__('Read Bank Slip'), () => nbi_read_slip_pe(frm), __('Bank'));
  },
});

function nbi_read_slip_pe(frm) {
  new frappe.ui.FileUploader({
    doctype: frm.doctype,
    docname: frm.doc.name,
    allow_multiple: false,
    restrictions: { allowed_file_types: ['.pdf', '.png', '.jpg', '.jpeg', '.webp'] },
    on_success(file_doc) {
      frappe.call({
        method: 'neotec_insight.neotec_insight.api.bank_reader.read_slip_into',
        args: { file_url: file_doc.file_url, target_doctype: 'Payment Entry', company: frm.doc.company },
        freeze: true,
        freeze_message: __('Reading slip…'),
        callback: ({ message }) => {
          if (!message) return;
          const fm = message.field_map || {};
          Object.keys(fm).forEach((k) => frm.set_value(k, fm[k]));
          frappe.show_alert({
            message: __('Filled from slip ({0}, {1}% conf.)', [message.method, Math.round((message.confidence || 0) * 100)]),
            indicator: 'green',
          });
          // Let the user choose the party from a rich, full-detail dropdown.
          nbi_pick_party_pe(frm, message.suggested_party_search);
        },
      });
    },
  });
}

function nbi_pick_party_pe(frm, search) {
  const ptype = frm.doc.payment_type === 'Receive' ? 'Customer' : 'Supplier';
  frappe.call({
    method: 'neotec_insight.neotec_insight.api.bank_reader.search_parties',
    args: { party_type: ptype, txt: search || '' },
    callback: ({ message }) => {
      const rows = message || [];
      const d = new frappe.ui.Dialog({
        title: __('Select {0}', [ptype]),
        size: 'large',
        primary_action_label: __('Skip'),
        primary_action: () => d.hide(),
      });
      const search_html = `<input type="text" class="form-control nbi-party-q" placeholder="${__('Search name / tax id…')}" value="${frappe.utils.escape_html(search || '')}">`;
      const render = (list) => list.map((r) => {
        const nm = r.supplier_name || r.customer_name || r.name;
        const grp = r.supplier_group || r.customer_group || '';
        return `<tr><td><b>${frappe.utils.escape_html(nm)}</b><br><span class="text-muted">${frappe.utils.escape_html(r.name)}</span></td>`
          + `<td>${frappe.utils.escape_html(r.tax_id || '')}</td><td>${frappe.utils.escape_html(grp)}</td>`
          + `<td>${frappe.utils.escape_html(r.default_currency || '')}</td>`
          + `<td><button class="btn btn-xs btn-primary nbi-pick" data-p="${frappe.utils.escape_html(r.name)}">${__('Use')}</button></td></tr>`;
      }).join('');
      const table = (list) => `<table class="table table-bordered"><thead><tr>`
        + `<th>${__('Name')}</th><th>${__('Tax ID')}</th><th>${__('Group')}</th><th>${__('Currency')}</th><th></th></tr></thead>`
        + `<tbody class="nbi-party-body">${render(list)}</tbody></table>`;
      d.$body.html(search_html + '<div style="margin-top:10px">' + table(rows) + '</div>');
      d.$body.on('click', '.nbi-pick', (e) => {
        frm.set_value('party_type', ptype);
        frm.set_value('party', $(e.currentTarget).data('p'));
        d.hide();
        frappe.show_alert({ message: __('Party set'), indicator: 'green' });
      });
      let t;
      d.$body.on('input', '.nbi-party-q', (e) => {
        clearTimeout(t);
        const q = e.target.value;
        t = setTimeout(() => {
          frappe.call({
            method: 'neotec_insight.neotec_insight.api.bank_reader.search_parties',
            args: { party_type: ptype, txt: q },
            callback: (r2) => { d.$body.find('.nbi-party-body').html(render(r2.message || [])); },
          });
        }, 300);
      });
      d.show();
    },
  });
}
