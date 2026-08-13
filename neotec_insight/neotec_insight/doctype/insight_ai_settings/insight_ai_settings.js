// Neotec — Insight AI Settings
// In the "Arabic Label Sources" table, once a Master (DocType) is chosen, the
// "Arabic Name Field" dropdown is filled with that master's text-like fields so
// the user can pick one instead of typing a fieldname.

frappe.ui.form.on('Insight AI Settings', {
  refresh(frm) {
    // Refresh field options for whichever row is currently open.
    (frm.doc.arabic_label_sources || []).forEach((row) => {
      if (row.source_doctype) nai_set_field_options(frm, row.doctype, row.name);
    });

    // Show the models actually pulled on the Ollama host, and let the user pick
    // one for the text / vision slots instead of typing a tag.
    frm.add_custom_button(__('Fetch Ollama Models'), () => {
      frappe.call({
        method: 'neotec_insight.neotec_insight.api.bank_reader.list_ollama_models',
        freeze: true,
        freeze_message: __('Querying Ollama…'),
        callback: ({ message }) => {
          if (!message || !message.ok) {
            frappe.msgprint({
              title: __('Could not reach Ollama'),
              message: (message && message.error) || __('No response from ') + (message && message.url),
              indicator: 'red',
            });
            return;
          }
          if (!message.models.length) {
            frappe.msgprint(__('Ollama is reachable but no models are pulled. Run e.g. "ollama pull qwen2.5".'));
            return;
          }
          const rows = message.models.map((m) =>
            `<tr><td><b>${frappe.utils.escape_html(m.name)}</b></td>`
            + `<td>${m.parameter_size || ''}</td><td>${m.size_gb} GB</td>`
            + `<td><button class="btn btn-xs btn-default nai-pick-text" data-m="${frappe.utils.escape_html(m.name)}">${__('Use as Text')}</button> `
            + `<button class="btn btn-xs btn-default nai-pick-vision" data-m="${frappe.utils.escape_html(m.name)}">${__('Use as Vision')}</button></td></tr>`
          ).join('');
          const d = new frappe.ui.Dialog({ title: __('Ollama Models'), size: 'large' });
          d.$body.html(
            `<p class="text-muted">${frappe.utils.escape_html(message.url)}</p>`
            + `<table class="table table-bordered"><thead><tr><th>${__('Model')}</th><th>${__('Params')}</th><th>${__('Size')}</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
          );
          d.$body.on('click', '.nai-pick-text', (e) => {
            frm.set_value('ollama_text_model', $(e.currentTarget).data('m')); d.hide();
            frappe.show_alert({ message: __('Text model set'), indicator: 'green' });
          });
          d.$body.on('click', '.nai-pick-vision', (e) => {
            frm.set_value('ollama_vision_model', $(e.currentTarget).data('m')); d.hide();
            frappe.show_alert({ message: __('Vision model set'), indicator: 'green' });
          });
          d.show();
        },
      });
    });
  },
});

frappe.ui.form.on('Insight Arabic Label Source', {
  source_doctype(frm, cdt, cdn) {
    // Reset any previously chosen field, then repopulate.
    frappe.model.set_value(cdt, cdn, 'label_field', '');
    nai_set_field_options(frm, cdt, cdn);
  },
  form_render(frm, cdt, cdn) {
    nai_set_field_options(frm, cdt, cdn);
  },
});

function nai_set_field_options(frm, cdt, cdn) {
  const row = locals[cdt][cdn];
  if (!row || !row.source_doctype) return;
  frappe.model.with_doctype(row.source_doctype, () => {
    const meta = frappe.get_meta(row.source_doctype);
    const TEXTY = ['Data', 'Select', 'Small Text', 'Text', 'Link', 'Read Only', 'Long Text'];
    const fields = (meta.fields || [])
      .filter((f) => f.fieldname && TEXTY.includes(f.fieldtype))
      .map((f) => f.fieldname);
    // Always allow the record's own name as a fallback choice.
    const opts = [''].concat(Array.from(new Set(fields))).join('\n');
    const grid = frm.fields_dict.arabic_label_sources.grid;
    grid.update_docfield_property('label_field', 'options', opts);
    grid.refresh();
  });
}
