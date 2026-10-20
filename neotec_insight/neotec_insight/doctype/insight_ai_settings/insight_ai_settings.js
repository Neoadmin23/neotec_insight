// v2.96.0 — test the AI connection with the saved settings.
frappe.ui.form.on("Insight AI Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test AI connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first."));
				return;
			}
			frappe.call({
				method: "neotec_insight.neotec_insight.api.ai.test_ai_connection",
				freeze: true,
				freeze_message: __("Asking the model…"),
				callback(r) {
					const m = r.message || {};
					const where = m.external
						? __("Outside your network — figures sent to the AI leave your servers.")
						: __("Inside your network.");
					const head = `<b>${frappe.utils.escape_html(m.provider || "")}</b> · ${frappe.utils.escape_html(m.model || "")} · ${m.ms || 0} ms<br>${where}`;
					if (m.ok) {
						frappe.msgprint({ title: __("AI connection works"), indicator: "green",
							message: `${head}<br><br>${__("Reply")}: ${frappe.utils.escape_html(m.reply || "")}` });
					} else {
						frappe.msgprint({ title: __("AI connection failed"), indicator: "red",
							message: `${head}<br><br>${frappe.utils.escape_html(m.error || "")}` });
					}
				},
			});
		});
	},
});
