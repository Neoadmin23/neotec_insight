from __future__ import annotations

from frappe.model.document import Document


class InsightBackupSnapshot(Document):
	"""A compressed copy of the Insight backup bundle, taken automatically.

	Written only by utils/snapshots.py. The payload is gzip+base64 JSON in the
	same format as Backup > Export, so any snapshot can be downloaded and
	imported on another site exactly like a manual backup."""
	pass
