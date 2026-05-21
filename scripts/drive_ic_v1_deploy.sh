#!/usr/bin/env bash
# Driver: chunked IC v1 prod deploy.
#
# Prereqs (built locally already this session):
#   - 11 base64 chunks at /tmp/chunk.aa through /tmp/chunk.ak
#     (10 x 20000 bytes + 1 x ~6604 bytes)
#   - Finalize script at scripts/ic_v1_deploy_finalize.sh
#
# Run from the repo root:
#   bash scripts/drive_ic_v1_deploy.sh
#
# Wall time ~5 min (11 az invocations at ~20-30s each, then ~30s for the
# final assembly + restart + verify). Each az call streams output back.
set -e
VM=tc-prod-vm
RG=rg-shared-prod
TMPDIR="${TMPDIR:-/tmp}"
SUFFIXES="aa ab ac ad ae af ag ah ai aj ak"

echo
echo "==================================================================="
echo " IC v1 prod deploy - chunked transport (11 chunks)"
echo "==================================================================="

# Phase 1: reset prod chunks dir
echo
echo "==> Resetting /tmp/ic_v1_chunks on prod"
cat > "$TMPDIR/ic_reset.sh" <<'RESET_EOF'
sudo rm -rf /tmp/ic_v1_chunks
mkdir -p /tmp/ic_v1_chunks
echo ready
RESET_EOF
az vm run-command invoke -n "$VM" -g "$RG" --command-id RunShellScript \
    --scripts "@$TMPDIR/ic_reset.sh" --query "value[0].message" -o tsv

# Phase 2: upload each chunk
i=1
for suffix in $SUFFIXES; do
    n=$(printf "%02d" $i)
    chunk="$TMPDIR/chunk.$suffix"
    script="$TMPDIR/ic_upload_$n.sh"

    if [ ! -f "$chunk" ]; then
        echo "FATAL: missing chunk $chunk"
        exit 2
    fi

    {
        echo "set -e"
        echo "mkdir -p /tmp/ic_v1_chunks"
        echo "cat > /tmp/ic_v1_chunks/chunk_${n}.b64 <<'CHUNK_EOF'"
        cat "$chunk"
        echo
        echo "CHUNK_EOF"
        echo "echo \"chunk_${n}.b64: \$(wc -c < /tmp/ic_v1_chunks/chunk_${n}.b64) bytes\""
    } > "$script"

    sz=$(wc -c < "$script")
    echo
    echo "==> Uploading chunk $n/11 (script=$sz bytes)"
    az vm run-command invoke -n "$VM" -g "$RG" --command-id RunShellScript \
        --scripts "@$script" --query "value[0].message" -o tsv
    i=$((i+1))
done

# Phase 3: final assembly + restart + verify
echo
echo "==================================================================="
echo " Final stage: assemble + extract + restart + verify"
echo "==================================================================="
az vm run-command invoke -n "$VM" -g "$RG" --command-id RunShellScript \
    --scripts @scripts/ic_v1_deploy_finalize.sh --query "value[0].message" -o tsv

echo
echo "==================================================================="
echo " Deploy driver complete."
echo "==================================================================="
