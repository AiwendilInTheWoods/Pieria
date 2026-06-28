"""Publisher tooling — generate an Ed25519 keypair and sign a Manifest v2 file in place.

The dependency-light CLI front door to the signing core in `publisher.py`. A publisher signs their
manifest so subscribers can verify it's untampered and (once their key is in the registry) show it as
a *verified* feed. The signature covers the canonical bytes (`federation.canonical_bytes`): the
manifest minus `signature`, JSON sorted-keys + compact + UTF-8.

  python -m tools.sign_manifest keygen
      → prints a private signing key + public key (base64). Keep the private key SECRET.

  python -m tools.sign_manifest sign manifest.json --key <private-b64> [--public <pub-b64>]
      → writes publisher.public_key + signature into manifest.json in place.

The Publisher Studio (the /api/publisher/* routes) wraps the same core; this CLI and
`tools/build_manifest` share it via `publisher.py` so there is one signing implementation.
"""

import argparse
import json
import sys

import publisher


def keygen() -> None:
    priv, pub = publisher.keygen()
    print("private_key (KEEP SECRET):", priv)
    print("public_key  (put in manifest publisher.public_key + the registry):", pub)


def sign(path: str, private_b64: str, public_b64: str | None) -> None:
    with open(path) as f:
        manifest = json.load(f)
    signed = publisher.sign_manifest(manifest, private_b64, public_b64)
    with open(path, "w") as f:
        json.dump(signed, f, indent=2, ensure_ascii=False)
    print(f"Signed {path} as publisher '{signed.get('publisher', {}).get('id')}'.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate keys / sign a Manifest v2 file.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="generate an Ed25519 keypair")
    sp = sub.add_parser("sign", help="sign a manifest file in place")
    sp.add_argument("path")
    sp.add_argument("--key", required=True, help="base64 private signing key")
    sp.add_argument("--public", help="base64 public key (derived from --key if omitted)")
    args = parser.parse_args(argv)
    if args.cmd == "keygen":
        keygen()
    elif args.cmd == "sign":
        sign(args.path, args.key, args.public)
    return 0


if __name__ == "__main__":
    sys.exit(main())
