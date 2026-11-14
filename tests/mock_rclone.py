#!/usr/bin/env python3
"""
Mock of the rclone CLI subset used by backup.py / setup.py, for offline tests.
The "Drive" is a local folder: gdrive:<path>  ->  $MOCK_RCLONE_ROOT/gdrive/<path>

Env:
  MOCK_RCLONE_ROOT        required, backing folder
  MOCK_RCLONE_REMOTES     comma list of configured remotes (default "gdrive")
  MOCK_RCLONE_MODE        ok (default) | network | auth   (failure for lsd/lsjson/copyto)
  MOCK_RCLONE_FAIL_UPLOADS  N => the first N uploads (copyto local->remote) fail with a network error
Error texts imitate real rclone messages; this is NOT real Google Drive.
"""
import json, os, shutil, sys, time

VALUE_FLAGS = {"--contimeout", "--timeout", "--retries", "--low-level-retries"}
root = os.environ["MOCK_RCLONE_ROOT"]
mode = os.environ.get("MOCK_RCLONE_MODE", "ok")
remotes = [r for r in os.environ.get("MOCK_RCLONE_REMOTES", "gdrive").split(",") if r]

argv, i = [], 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a in VALUE_FLAGS:
        i += 2
        continue
    if a.startswith("--"):
        i += 1
        continue
    argv.append(a)
    i += 1
cmd, args = argv[0], argv[1:]


def fail(code, msg):
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def remote_path(spec):
    name, _, path = spec.partition(":")
    return name, os.path.join(root, name, path.strip("/"))


def inject_failure():
    if mode == "network":
        fail(1, 'Failed to create file system for "gdrive:": couldn\'t get token: Post '
                '"https://oauth2.googleapis.com/token": dial tcp: lookup oauth2.googleapis.com: no such host')
    if mode == "auth":
        fail(1, 'Failed to create file system for "gdrive:": couldn\'t get token: oauth2: cannot fetch token: '
                '400 Bad Request\nResponse: {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}')


if cmd == "version":
    print("rclone v1.66.0 (mock)")
elif cmd == "listremotes":
    for r in remotes:
        print(r + ":")
elif cmd == "lsd":
    inject_failure()
    name, p = remote_path(args[0])
    if name not in remotes:
        fail(3, "didn't find section in config file")
    if os.path.isdir(p):
        for d in sorted(os.listdir(p)):
            if os.path.isdir(os.path.join(p, d)):
                print(f"          -1 2026-01-01 00:00:00        -1 {d}")
elif cmd == "lsjson":
    inject_failure()
    name, p = remote_path(args[0])
    if not os.path.isdir(p):
        fail(3, "directory not found")
    out = []
    for f in sorted(os.listdir(p)):
        fp = os.path.join(p, f)
        out.append({"Path": f, "Name": f, "Size": os.path.getsize(fp) if os.path.isfile(fp) else -1,
                    "ModTime": "2026-01-01T00:00:00Z", "IsDir": os.path.isdir(fp)})
    print(json.dumps(out))
elif cmd == "check":
    local, remote = args[0], args[1]
    _, remote_file = remote_path(remote)
    if not os.path.isfile(local) or not os.path.isfile(remote_file):
        fail(1, "check failed: missing file")
    if open(local, "rb").read() != open(remote_file, "rb").read():
        fail(1, "check failed: content mismatch")
elif cmd == "copyto":
    inject_failure()
    src, dst = args

    def is_remote(x):
        head = x.partition(":")[0]
        return ":" in x and "/" not in head and os.sep not in head and not os.path.isabs(x)

    if is_remote(dst):  # upload
        counter = os.path.join(root, ".fail_uploads")
        n = int(os.environ.get("MOCK_RCLONE_FAIL_UPLOADS", "0"))
        if n:
            done = int(open(counter).read()) if os.path.exists(counter) else 0
            if done < n:
                open(counter, "w").write(str(done + 1))
                fail(1, "Failed to copyto: dial tcp 142.250.0.1:443: i/o timeout")
        _, p = remote_path(dst)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(src, p)
    else:  # download
        _, p = remote_path(src)
        if not os.path.isfile(p):
            fail(4, "Failed to copyto: object not found")
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copyfile(p, dst)
else:
    fail(1, f"mock rclone: unsupported command {cmd}")
