"""Historical and non-subject evidence cannot silently supervise image heads."""
from studio_jobs import StudioJobs


class GuardedJobs(StudioJobs):
    def __init__(self, store, assessments):
        self.assessments = assessments
        super().__init__(store)

    def _snapshot(self):
        rows, revision = super()._snapshot()
        histories = {}
        for row in rows:
            key = row["property_id"]
            if key not in histories:
                histories[key] = self.assessments.history(key)
            history = histories[key]
            if (history and not history["timing_verified"]) or row.get("photo_context") in {
                "shared_amenity", "floor_plan", "unrelated",
            }:
                row.update(room=None, features={}, preference=None,
                           label_exclusion="Historical era unverified or non-subject photo")
        return rows, revision

    def start(self, payload, kind="train"):
        if kind == "train":
            # Old previews may predate an era/context correction. Never start
            # fitting from those snapshots after evidence has been quarantined.
            from pilot import read_json
            identifier = payload.get("id", "")
            if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
                raise ValueError("Unknown job preview")
            snapshot = read_json(self.folder / identifier / "snapshot.json")
            current, _ = self._snapshot()
            from model_loop import fingerprint
            if fingerprint(snapshot["examples"]) != fingerprint(current):
                raise ValueError("Reviews or eligibility changed. Create a new training preview.")
        return super().start(payload, kind)

