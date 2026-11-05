"""Create, list and revoke client API keys (run ON the API server, as the user that owns the keys file).

    python -m client_api.manage_keys create "Client group name" [--note "who asked, how it was sent"]
    python -m client_api.manage_keys list
    python -m client_api.manage_keys revoke k_1a2b3c4d

`create` prints the key ONCE. Only its hash is kept, so a lost key can't be recovered: revoke it and create another.
Send the key to the client through a password-manager share or a one-time secret link, never in plain email or chat.
A revoke takes effect on the client's next call (the server re-reads the file when it changes).
The keys file is API_KEYS_FILE (default /etc/river-fob-api/keys.json).
"""
import argparse
import sys

from client_api.auth import KeyStore


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("client")
    c.add_argument("--note", default="")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("key_id")
    a = ap.parse_args(argv)
    store = KeyStore()
    if a.cmd == "create":
        raw, rec = store.create(a.client, a.note)
        print(f"Created {rec['id']} for {rec['client']}. The key (shown once, store it now):\n\n    {raw}\n")
        return 0
    if a.cmd == "list":
        for rec in store.list():
            state = "active" if rec.get("active", True) else f"REVOKED {rec.get('revoked', '')}"
            print(f"{rec['id']}  {rec['client']}  created {rec['created']}  {state}  {rec.get('note', '')}")
        return 0
    if store.revoke(a.key_id):
        print(f"Revoked {a.key_id}.")
        return 0
    print(f"No active key {a.key_id}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
