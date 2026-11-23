import json
from pathlib import Path

import pytest

import sharepoint
from sharepoint import Graph, GraphError


class FakeGraph(Graph):
    """Graph with a scripted transport: each _open call pops the next (status, body) response."""

    def __init__(self, responses):
        super().__init__(token_provider=lambda: "t", sleep=lambda s: None)
        self.responses = list(responses)
        self.calls = []

    def _open(self, req):
        self.calls.append((req.get_method(), req.full_url, req.data))
        status, body = self.responses.pop(0)
        return status, json.dumps(body).encode() if isinstance(body, (dict, list)) else body


# --- pure helpers ---

def test_group_payload_is_a_private_unified_group():
    p = sharepoint.group_payload("OL Restricted", "ol-restricted", "desc")
    assert p["groupTypes"] == ["Unified"] and p["visibility"] == "Private"
    assert p["mailNickname"] == "ol-restricted" and p["securityEnabled"] is False


def test_group_payload_rejects_unknown_visibility():
    with pytest.raises(ValueError):
        sharepoint.group_payload("t", "n", visibility="HiddenMembership")


def test_site_web_url():
    assert sharepoint.site_web_url("t.sharepoint.com", "ol-knowledge") == "https://t.sharepoint.com/sites/ol-knowledge"


def test_remote_path_uses_forward_slashes(tmp_path):
    f = tmp_path / "safety" / "loto.docx"
    f.parent.mkdir()
    f.write_text("x")
    assert sharepoint.remote_path(tmp_path, f) == "safety/loto.docx"


def test_iter_files_filters_by_suffix_and_sorts(tmp_path):
    for name in ("b.docx", "a.pdf", "skip.txt", "sub/c.DOCX"):
        p = tmp_path / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("x")
    names = [p.relative_to(tmp_path).as_posix() for p in sharepoint.iter_files(tmp_path)]
    assert names == ["a.pdf", "b.docx", "sub/c.DOCX"]


def test_quote_path_encodes_each_segment_but_keeps_slashes():
    assert sharepoint.quote_path("my dir/a b.docx") == "my%20dir/a%20b.docx"


# --- Graph client ---

def test_request_returns_parsed_json():
    g = FakeGraph([(200, {"value": [1]})])
    assert g.request("GET", "/groups") == {"value": [1]}
    assert g.calls[0][1].startswith("https://graph.microsoft.com/v1.0/groups")


def test_request_retries_throttling_then_succeeds():
    g = FakeGraph([(429, {}), (503, {}), (200, {"ok": True})])
    assert g.request("GET", "/x") == {"ok": True}
    assert len(g.calls) == 3


def test_request_raises_graph_error_with_message():
    g = FakeGraph([(403, {"error": {"message": "nope"}})])
    with pytest.raises(GraphError) as exc:
        g.request("GET", "/x")
    assert exc.value.status == 403 and "nope" in str(exc.value)


def test_request_allow_returns_none_for_listed_status():
    g = FakeGraph([(404, {"error": {"message": "gone"}})])
    assert g.request("GET", "/x", allow=(404,)) is None


def test_request_gives_up_after_repeated_throttling():
    g = FakeGraph([(429, {})] * 4)
    with pytest.raises(GraphError):
        g.request("GET", "/x")


def test_ensure_group_returns_existing_without_creating():
    g = FakeGraph([(200, {"value": [{"id": "g1"}]})])
    assert g.ensure_group("T", "nick") == ("g1", False)
    assert len(g.calls) == 1


def test_ensure_group_creates_when_missing():
    g = FakeGraph([(200, {"value": []}), (201, {"id": "g2"})])
    assert g.ensure_group("T", "nick") == ("g2", True)
    method, _url, data = g.calls[1]
    assert method == "POST" and json.loads(data)["mailNickname"] == "nick"


def test_add_member_reports_true_then_false_when_already_member():
    g = FakeGraph([(200, {"id": "u1"}), (204, b""),
                   (200, {"id": "u1"}), (400, {"error": {"message": "One or more added object references already exist"}})])
    assert g.add_member("g1", "jane@x.com") is True
    assert g.add_member("g1", "jane@x.com") is False


def test_add_member_propagates_other_errors():
    g = FakeGraph([(200, {"id": "u1"}), (403, {"error": {"message": "denied"}})])
    with pytest.raises(GraphError):
        g.add_member("g1", "jane@x.com")


def test_wait_for_site_polls_until_ready():
    g = FakeGraph([(404, {"error": {"message": "x"}}), (200, {"webUrl": "https://t/sites/n"})])
    assert g.wait_for_site("g1", interval=0) == "https://t/sites/n"


def test_upload_file_puts_bytes_to_the_encoded_path(tmp_path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"data")
    g = FakeGraph([(201, {"name": "a.docx"})])
    g.upload_file("g1", f, "my dir/a.docx")
    method, url, data = g.calls[0]
    assert method == "PUT" and data == b"data"
    assert url.endswith("/groups/g1/drive/root:/my%20dir/a.docx:/content")


def test_upload_file_rejects_large_files(tmp_path):
    f = tmp_path / "big.docx"
    f.write_bytes(b"x" * (sharepoint.MAX_SIMPLE_UPLOAD + 1))
    with pytest.raises(ValueError):
        FakeGraph([]).upload_file("g1", f, "big.docx")


def test_upload_tree_uploads_every_supported_file(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.docx").write_bytes(b"1")
    (tmp_path / "b.docx").write_bytes(b"2")
    g = FakeGraph([(201, {}), (201, {})])
    assert g.upload_tree("g1", tmp_path) == ["b.docx", "s/a.docx"]


def test_delete_group_returns_false_when_absent_and_true_when_deleted():
    assert FakeGraph([(200, {"value": []})]).delete_group("n") is False
    g = FakeGraph([(200, {"value": [{"id": "g1"}]}), (204, b"")])
    assert g.delete_group("n") is True
    assert g.calls[1][0] == "DELETE"


def test_delete_group_with_purge_also_removes_the_soft_deleted_copy():
    g = FakeGraph([(200, {"value": [{"id": "g1"}]}), (204, b""), (204, b"")])
    assert g.delete_group("n", purge=True) is True
    assert g.calls[2][0] == "DELETE" and g.calls[2][1].endswith("/directory/deletedItems/g1")


def test_delete_group_purge_tolerates_a_missing_soft_deleted_copy():
    g = FakeGraph([(200, {"value": [{"id": "g1"}]}), (204, b""), (404, {"error": {"message": "gone"}})])
    assert g.delete_group("n", purge=True) is True


def test_cli_status_reports_existence(capsys):
    g = FakeGraph([(200, {"value": [{"id": "g1"}]})])
    assert sharepoint.main(["status", "--nick", "n"], graph=g) == 0
    assert json.loads(capsys.readouterr().out) == {"exists": True, "group_id": "g1"}
