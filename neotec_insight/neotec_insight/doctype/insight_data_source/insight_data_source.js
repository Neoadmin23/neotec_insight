// v2.95.0 — test the connection and read the table structure.
frappe.ui.form.on("Insight Data Source", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Test connection & read tables"), () => {
			frappe.call({
				method: "neotec_insight.neotec_insight.api.sources.test_source",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Connecting…"),
				callback(r) {
					if (r.message) {
						frappe.msgprint(r.message.message);
						frm.reload_doc();
					}
				},
			});
		});
	},
});
