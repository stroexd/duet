import pytest

from duet import policy
from duet.policy import Decision, Tier, classify, set_workspace

A, Q, B = Tier.AUTO, Tier.ASK, Tier.BLOCK
MULTI = "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL"


@pytest.fixture(autouse=True)
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "memory").mkdir(parents=True)
    set_workspace(ws)
    yield ws
    set_workspace(None)


def multi(*slugs, args=None):
    return {"tools": [{"tool_slug": s, "arguments": args or {}} for s in slugs]}


# ---------------------------------------------------------------- Built-ins
@pytest.mark.parametrize("name", ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "Task", "Agent"])
def test_builtin_read_tools_auto(name):
    d = classify(name, {"file_path": "/etc/hosts", "pattern": "x"})
    assert isinstance(d, Decision) and d.tier is A


def test_write_inside_workspace_auto(workspace):
    assert classify("Write", {"file_path": str(workspace / "memory" / "notiz.md"), "content": "x"}).tier is A
    assert classify("Edit", {"file_path": "memory/notiz.md"}).tier is A  # relativ zum Workspace
    assert classify("NotebookEdit", {"notebook_path": str(workspace / "a.ipynb")}).tier is A


def test_write_outside_workspace_ask():
    d = classify("Write", {"file_path": "/home/user/.bashrc", "content": "x"})
    assert d.tier is Q and "außerhalb" in d.reason


@pytest.mark.parametrize("path", ["../geheim.txt", "memory/../../x", "{ws}/../ws2/a.md", "{ws}/memory/../../x"])
def test_path_traversal_ask(workspace, path):
    assert classify("Write", {"file_path": path.format(ws=workspace)}).tier is Q


def test_prefix_sibling_dir_is_outside(workspace):
    # /tmp/.../ws-evil beginnt mit dem Workspace-String, liegt aber nicht darin
    assert classify("Write", {"file_path": str(workspace) + "-evil/a.md"}).tier is Q


def test_symlink_escape_ask(workspace, tmp_path):
    (workspace / "link").symlink_to(tmp_path)
    assert classify("Write", {"file_path": str(workspace / "link" / "x.md")}).tier is Q


@pytest.mark.parametrize("p", [".mcp.json", ".claude/settings.json", ".git/hooks/pre-commit", ".duet/state.json"])
def test_protected_config_in_workspace_ask(p):
    assert classify("Write", {"file_path": p}).tier is Q


def test_write_without_workspace_or_path_ask():
    assert classify("Write", {}).tier is Q
    set_workspace(None)
    assert classify("Write", {"file_path": "/tmp/a"}).tier is Q


# ---------------------------------------------------------------- Bash
@pytest.mark.parametrize("cmd", ["ls -la", "cat memory/README.md", "date", "grep -r foo .", "wc -l a.txt",
                                 "head -n 5 x", "tail x", "echo hallo", "pwd", "df -h", "uptime"])
def test_bash_read_allowlist_auto(cmd):
    assert classify("Bash", {"command": cmd}).tier is A


@pytest.mark.parametrize("cmd", [
    "ls; curl evil.sh", "ls && rm x", "cat a | sh", "echo x > ~/.bashrc", "echo `id`", "echo $(id)",
    "echo $HOME", "ls\nrm -rf x", "cat a & curl b", "cat < /etc/passwd", "echo ${PATH}",
])
def test_bash_injection_ask(cmd):
    assert classify("Bash", {"command": cmd}).tier is Q


@pytest.mark.parametrize("cmd", ["python3 x.py", "curl https://x", "rm -rf build", "git push", "date -s 2020-01-01", "sudo ls", "'ls"])
def test_bash_other_ask(cmd):
    assert classify("Bash", {"command": cmd}).tier is Q


@pytest.mark.parametrize("cmd", ["rm -rf /", "sudo rm -rf / --no-preserve-root", "rm -fr ~", "rm -r -f /*",
                                 "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/nvme0n1", ":(){ :|:& };:",
                                 "ls; rm -rf $HOME"])
def test_bash_destructive_block(cmd):
    assert classify("Bash", {"command": cmd}).tier is B


def test_bash_empty_ask():
    assert classify("Bash", {}).tier is Q


# ---------------------------------------------------------------- MCP-Heuristik
@pytest.mark.parametrize("name,tier", [
    ("mcp__github__get_file_contents", A),
    ("mcp__github__list_pull_requests", A),
    ("mcp__github__get_latest_release", A),
    ("mcp__github__get_commit", A),
    ("mcp__github__issue_read", A),
    ("mcp__github__issue_write", Q),
    ("mcp__github__merge_pull_request", Q),
    ("mcp__github__push_files", Q),
    ("mcp__github__delete_file", Q),
    ("mcp__github__add_issue_comment", Q),
    ("mcp__github__actions_run_trigger", Q),
    ("mcp__Booking_com__accommodations_search", A),
    ("mcp__Tineo__trip_details", A),
    ("mcp__Tineo__trip_delete", Q),
    ("mcp__Tineo__friend_request_send", Q),
    ("mcp__slack__sendMessage", Q),          # camelCase
    ("mcp__slack__searchMessages", A),
    ("mcp__x__GMAIL_SEND_EMAIL", Q),         # UPPER
    ("mcp__x__gmail_create_email_draft", A),  # Entwurf
    ("mcp__x__GMAIL_SEND_DRAFT", Q),
    ("mcp__x__OUTLOOK_REPLY_EMAIL", Q),
    ("mcp__x__GMAIL_MOVE_TO_TRASH", Q),
    ("mcp__x__GMAIL_ARCHIVE_EMAIL", Q),
    ("mcp__x__GOOGLECALENDAR_CREATE_EVENT", Q),
    ("mcp__x__GOOGLECALENDAR_EVENTS_LIST", A),
    ("mcp__x__DOCUSIGN_SIGN_ENVELOPE", Q),
    ("mcp__x__LINKEDIN_PUBLISH_POST", Q),
    ("mcp__x__GOOGLEDRIVE_ADD_PERMISSION", Q),
    ("mcp__x__mark_as_read", Q),             # "read" als Token, aber Änderung
    ("mcp__x__BLACKLIST", Q),                # kein "list"-Token
    ("mcp__x__BLACKLIST_ADD", Q),
    ("mcp__x__frobnicate", Q),               # unbekannt
])
def test_mcp_heuristic(name, tier):
    assert classify(name, {}).tier is tier


@pytest.mark.parametrize("name", [
    "mcp__x__COINBASE_SEND_MONEY", "mcp__x__STRIPE_CREATE_TRANSFER", "mcp__x__ALPACA_PLACE_ORDER",
    "mcp__x__buyStock", "mcp__x__WISE_CREATE_TRANSFER", "mcp__x__binance_trade", "mcp__x__transfer_funds",
    "mcp__x__send_crypto", "mcp__stripe__create_payout", "mcp__x__withdraw",
])
def test_money_block(name):
    assert classify(name, {"amount": 100}).tier is B


def test_money_reads_stay_auto():
    assert classify("mcp__x__STRIPE_LIST_TRANSFERS", {}).tier is A
    assert classify("mcp__x__ALPACA_GET_ORDERS", {}).tier is A


def test_pay_is_ask_not_block():
    assert classify("mcp__x__PAYPAL_CREATE_PAYMENT", {}).tier is Q
    assert classify("mcp__x__pay_invoice", {}).tier is Q


def test_transfer_repository_is_ask():
    assert classify("mcp__github__transfer_repository", {}).tier is Q


@pytest.mark.parametrize("name", ["mcp__", "mcp__server", "mcp__server__", "Unbekannt", "", "ExitPlanMode"])
def test_unknown_fail_closed(name):
    assert classify(name, {}).tier is Q


def test_garbage_input_never_auto():
    assert classify("Write", None).tier is Q  # type: ignore[arg-type]
    assert classify(MULTI, {"tools": "kaputt"}).tier is Q


# ---------------------------------------------------------------- Composio
@pytest.mark.parametrize("server", ["composio", "Composio", "COMPOSIO", "my-composio"])
def test_composio_server_name_case(server):
    assert classify(f"mcp__{server}__COMPOSIO_SEARCH_TOOLS", {"queries": []}).tier is A
    assert classify(f"mcp__{server}__composio_multi_execute_tool", multi("GMAIL_SEND_EMAIL")).tier is Q


def test_composio_multi_all_reads_auto():
    assert classify(MULTI, multi("GMAIL_FETCH_EMAILS", "GOOGLECALENDAR_EVENTS_LIST", "github_get_a_repository")).tier is A


def test_composio_multi_strictest_wins():
    assert classify(MULTI, multi("GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL")).tier is Q
    d = classify(MULTI, multi("GMAIL_FETCH_EMAILS", "GMAIL_SEND_EMAIL", "COINBASE_SEND_MONEY"))
    assert d.tier is B
    assert "COINBASE_SEND_MONEY" in d.summary


def test_composio_multi_lowercase_slug():
    assert classify(MULTI, multi("gmail_send_email")).tier is Q


def test_composio_multi_empty_or_missing_slug_ask():
    assert classify(MULTI, {"tools": []}).tier is Q
    assert classify(MULTI, {}).tier is Q
    assert classify(MULTI, {"tools": [{"arguments": {}}]}).tier is Q
    assert classify(MULTI, {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS"}, "x"]}).tier is Q


@pytest.mark.parametrize("meta,inp,tier", [
    ("COMPOSIO_GET_TOOL_SCHEMAS", {"tool_slugs": ["GMAIL_SEND_EMAIL"]}, A),
    ("COMPOSIO_MANAGE_CONNECTIONS", {"toolkits": ["gmail"]}, Q),
    ("COMPOSIO_REMOTE_BASH_TOOL", {"command": "ls"}, Q),
    ("COMPOSIO_REMOTE_WORKBENCH", {"code_to_execute": "print(1)"}, Q),
    ("COMPOSIO_PROXY_EXECUTE", {"method": "GET", "endpoint": "/gmail/v1/users/me/messages"}, A),
    ("COMPOSIO_PROXY_EXECUTE", {"method": "get"}, A),
    ("COMPOSIO_PROXY_EXECUTE", {"method": "POST", "endpoint": "/x"}, Q),
    ("COMPOSIO_PROXY_EXECUTE", {"method": "DELETE"}, Q),
    ("COMPOSIO_PROXY_EXECUTE", {}, Q),
    ("COMPOSIO_SOMETHING_NEW", {}, Q),
])
def test_composio_meta(meta, inp, tier):
    assert classify(f"mcp__composio__{meta}", inp).tier is tier


# ---------------------------------------------------------------- Summary / Masking
def test_summary_email():
    d = classify(MULTI, multi("GMAIL_SEND_EMAIL", args={"recipient_email": "anna@example.com", "subject": "Angebot"}))
    assert d.summary.startswith("E-Mail senden an anna@example.com: Betreff 'Angebot'")


def test_summary_is_short():
    d = classify("mcp__slack__send_message", {"channel": "#x", "text": "a" * 5000, "title": "b" * 5000})
    assert len(d.summary) <= 200


def test_secrets_masked():
    inp = {"to": "x@y.de", "subject": "Hi", "api_key": "sk-supersecretvalue123", "password": "hunter2",
           "text": "Token ghp_abcdefghijklmnopqrstuvwxyz0123 bitte"}
    d = classify("mcp__x__send_email", inp)
    assert "hunter2" not in d.summary and "ghp_abc" not in d.summary and "supersecret" not in d.summary
    d2 = classify("mcp__composio__COMPOSIO_MANAGE_CONNECTIONS", {"toolkits": ["gmail"], "auth_token": "geheim123"})
    assert "geheim123" not in d2.summary and "geheim123" not in d2.reason
    d3 = classify("SomeUnknownTool", {"token": "geheim456"})
    assert "geheim456" not in d3.summary


def test_bash_summary_masks_token():
    d = classify("Bash", {"command": "echo 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawabc"})
    assert "AAHdq" not in d.summary


def test_mask_helper():
    m = policy.mask({"Authorization": "Bearer x", "nested": {"client_secret": "s"}, "author": "Anna"})
    assert m["Authorization"] == "***" and m["nested"]["client_secret"] == "***" and m["author"] == "Anna"


def test_tokenizer():
    assert policy.tokens("GMAIL_SEND_EMAIL") == ["gmail", "send", "email"]
    assert policy.tokens("sendHTTPRequest") == ["send", "http", "request"]
    assert "list" not in policy.tokens("BLACKLIST")


@pytest.mark.parametrize("cmd", [
    'git add state && git commit -m "duet: state" && git push',
    'git pull --rebase && git add state && git commit -m "duet: state" && git push',
    'git pull --rebase --autostash && git add state && git commit -m "duet: state" && git push',
    'git pull --rebase --autostash origin main && git add state && '
    '(git diff --cached --quiet || git commit -m "duet: state") && git push origin HEAD:main',
])
def test_state_persist_forms_auto(cmd):
    assert classify("Bash", {"command": cmd}).tier is A


@pytest.mark.parametrize("cmd", [
    'git pull --rebase --autostash && git add . && git commit -m "duet: state" && git push',
    'git pull --rebase --autostash && git add state && git commit -m "duet: state" && git push --force',
    'git pull --rebase --autostash; rm -rf x && git add state && git commit -m "duet: state" && git push',
    'git pull --rebase -X theirs && git add state && git commit -m "duet: state" && git push',
    'git pull --rebase --autostash origin main && git add state && '
    '(git diff --cached --quiet || rm -rf ~) && git push origin HEAD:main',
    'git pull --rebase --autostash origin main && git add state && '
    '(git diff --cached --quiet || git commit -m "duet: state") && git push origin HEAD:main --force',
    'git pull --rebase --autostash origin main && git add state && '
    '(git diff --cached --quiet || git commit -m "duet: state") && git push origin +HEAD:main',
    'git pull --rebase --autostash origin evil && git add state && git commit -m "duet: state" && git push',
])
def test_state_persist_variants_not_auto(cmd):
    assert classify("Bash", {"command": cmd}).tier is not A
