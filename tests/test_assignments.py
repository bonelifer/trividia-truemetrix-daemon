from __future__ import annotations

from trividia_truemetrix_daemon.assignments import AssignmentStore, resolve_profile
from trividia_truemetrix_daemon.config import ProfileConfig, ProfilesConfig


def test_assignment_store_round_trips(tmp_path):
    store = AssignmentStore(str(tmp_path / "assignments.json"))
    assert store.get("dev-1") is None
    store.set("dev-1", "Alice")
    assert store.get("dev-1") == "Alice"


def test_assignment_store_overwrites_on_re_assignment(tmp_path):
    store = AssignmentStore(str(tmp_path / "assignments.json"))
    store.set("dev-1", "Alice")
    store.set("dev-1", "Bob")
    assert store.get("dev-1") == "Bob"


def test_assignment_store_persists_across_instances(tmp_path):
    path = str(tmp_path / "assignments.json")
    AssignmentStore(path).set("dev-1", "Alice")
    assert AssignmentStore(path).get("dev-1") == "Alice"


def test_resolve_profile_prefers_static_over_dynamic(tmp_path):
    profiles = ProfilesConfig(
        profiles={
            "Alice": ProfileConfig(
                full_name="Alice Smith", email="", notes="", device_ids=("dev-1",),
                sliding_scale=(),
            )
        }
    )
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    assignments.set("dev-1", "Bob")  # dynamic claim, should lose to static

    assert resolve_profile("dev-1", profiles, assignments) == "Alice"


def test_resolve_profile_falls_back_to_dynamic(tmp_path):
    profiles = ProfilesConfig(profiles={})
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    assignments.set("dev-1", "Bob")

    assert resolve_profile("dev-1", profiles, assignments) == "Bob"


def test_resolve_profile_none_when_unassigned(tmp_path):
    profiles = ProfilesConfig(profiles={})
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    assert resolve_profile("dev-1", profiles, assignments) is None
