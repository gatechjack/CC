# Bitunix fresh paper observation window — evaluation (post vol-classifier fix `7834375`)

**Verified UTC at write:** 2026-06-09T10:31:57Z (local `date -u`).
**Session:** operator-supervised, **read-only prod** (operator runs all SSH). No prod writes, no code change, **no `execution_mode` flip**.
**Window start:** 2026-06-09T03:49:41Z (prod MainPID 2397472, ActiveEnter). At write, window age ≈ **6h 42m**.
**Branch:** `bitunix-fresh-window-eval-2026-06-09` (worktree-isolated, off `7d164d6`).

## Premises verified against ground truth (not memory)
- Prod DB: `/home/azureuser/trading_corp/data/trading_corp.db` (from probe_a1/probe_a4, investigation branch).
- `htf_gate_decision` payload keys are **top-level**: `volatility_tier`, `atr_pct_d1`, `size_multiplier`, `hard_zero_reason`, `mode` — verified `bitunix_futures_observer.py:1102-1130`.
- `would_have_placed` (kind) + `trade_plan_decision` (kind) both write **`actor='bitunix_futures'`** — verified `bitunix_futures_observer.py:2483-2487, 2320-2326`. Paper fire also inserts `paper_trade_record` with `division='bitunix_futures'` (`:2489-2497`).
- `paper_trade_record.result` ∈ {`win`,`loss`,`open`,`expired`}; `actual_r_multiple`, `actual_pnl_dollars` NULL until replay resolves — verified `persistence/db.py:123-157`.
- Pre-fix prod payload (FINDINGS, 06-08T20:28:28Z): `volatility_tier=extreme, atr_pct_d1=4.07, size_multiplier=0.0, hard_zero_reason=vol_tier_extreme`. Last working fire 06-02T22:15:01Z: `high / 2.92 / 1.0 / null`. Current BTC ATR ~4% (deploy_log) → squarely in the [3,5) band the fix targets.

---

## STATUS: ⏳ AWAITING OPERATOR SSH OUTPUT
I am read-only and cannot query prod. The two probes below are the deliverable. **Run Step 1 first**; only proceed to Step 2 if Step 1 **PASSES**. Paste the raw output back and I will produce the live-readiness read.

---

## Step 1 — confirm F-5 activation (FIRST ACTION)

**Paste-safe one-liner (PowerShell → SSH; single physical line):**
```
ssh azureuser@trading.jacksumner.com 'echo IyEvdXNyL2Jpbi9lbnYgYmFzaAojIFN0ZXAgMSDigJQgRi01IHZvbC1jbGFzc2lmaWVyIGFjdGl2YXRpb24gcHJvYmUgKGZyZXNoIHdpbmRvdywgcG9zdC1maXggNzgzNDM3NSkuCiMgQ29uZmlybXMsIGZvciB0aGUgZnJlc2ggcGFwZXIgd2luZG93IHN0YXJ0aW5nIDIwMjYtMDYtMDlUMDM6NDk6NDFaOgojICAgKGEpIGh0Zl9nYXRlX2RlY2lzaW9uIHJvd3Mgd2l0aCBhdHJfcGN0X2QxIGluIFszLDUpIG5vdyBjbGFzc2lmeQojICAgICAgIHZvbGF0aWxpdHlfdGllcj0iaGlnaCIsIHNpemVfbXVsdGlwbGllcj0xLjAsIGhhcmRfemVyb19yZWFzb249bnVsbAojICAgICAgIChwcmUtZml4IHRoZXkgd2VyZSAiZXh0cmVtZSIgLyAwLjAgLyAidm9sX3RpZXJfZXh0cmVtZSIpOwojICAgKGIpIHRyYWRlX3BsYW5fZGVjaXNpb24gLyB3b3VsZF9oYXZlX3BsYWNlZCBmaXJpbmcgaGFzIHJlc3VtZWQKIyAgICAgICAoZmlyc3QgYml0dW5peCBmaXJlcyBzaW5jZSAyMDI2LTA2LTAyVDIyOjE1WiBkb3JtYW5jeSkuCiMgUGF5bG9hZCBrZXlzIHZlcmlmaWVkIGFnYWluc3QgYml0dW5peF9mdXR1cmVzX29ic2VydmVyLnB5OjExMDItMTEzMCAodG9wLWxldmVsKS4KIyBhY3Rvcj0nYml0dW5peF9mdXR1cmVzJyBmb3IgaHRmX2dhdGVfZGVjaXNpb24gLyB0cmFkZV9wbGFuX2RlY2lzaW9uIC8gd291bGRfaGF2ZV9wbGFjZWQuCiMgUkVBRC1PTkxZLiBTRUxFQ1Qgb25seS4gTm8gd3JpdGVzLgpzZXQgLXVvIHBpcGVmYWlsCkRCPS9ob21lL2F6dXJldXNlci90cmFkaW5nX2NvcnAvZGF0YS90cmFkaW5nX2NvcnAuZGIKVz0nMjAyNi0wNi0wOVQwMzo0OTo0MSswMDowMCcKCmVjaG8gIj09PSBDT05URVhUID09PSIKZWNobyAiaG9zdD0kKGhvc3RuYW1lKSBub3dfdXRjPSQoZGF0ZSAtdSArJVktJW0tJWRUJUg6JU06JVNaKSB3aW5kb3dfc3RhcnQ9JFciCmlmIFsgLWYgIiREQiIgXTsgdGhlbiBlY2hvICJkYj1wcmVzZW50IHNpemVfYnl0ZXM9JChzdGF0IC1jICVzICIkREIiKSI7IGVsc2UgZWNobyAiZGI9TUlTU0lORyBhdCAkREIiOyBmaQplY2hvCgplY2hvICI9PT0gUzFhOiBBTEwgaHRmX2dhdGVfZGVjaXNpb24gcm93cyBzaW5jZSB3aW5kb3cgc3RhcnQgKHZvbF90aWVyIHZzIEFUUiBiYW5kKSA9PT0iCnNxbGl0ZTMgLWhlYWRlciAtY29sdW1uICIkREIiICJTRUxFQ1QgdHMsIGpzb25fZXh0cmFjdChwYXlsb2FkX2pzb24sJ1wkLnZvbGF0aWxpdHlfdGllcicpIEFTIHZvbF90aWVyLCBST1VORChqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5hdHJfcGN0X2QxJyksMykgQVMgYXRyX3BjdCwganNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuc2l6ZV9tdWx0aXBsaWVyJykgQVMgc2l6ZV9tdWx0LCBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5oYXJkX3plcm9fcmVhc29uJykgQVMgaGFyZF96ZXJvLCBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5tb2RlJykgQVMgbW9kZSBGUk9NIGF1ZGl0X2V2ZW50IFdIRVJFIGtpbmQ9J2h0Zl9nYXRlX2RlY2lzaW9uJyBBTkQgdHM+PSckVycgT1JERVIgQlkgdHM7IgplY2hvCgplY2hvICI9PT0gUzFiOiBBQ1RJVkFUSU9OIEdBVEUg4oCUIHJvd3Mgd2l0aCBBVFIgaW4gWzMsNSk6IHRpZXIgTVVTVCBiZSAnaGlnaCcsIHNpemVfbXVsdCAxLjAsIGhhcmRfemVybyBOVUxMID09PSIKc3FsaXRlMyAtaGVhZGVyIC1jb2x1bW4gIiREQiIgIlNFTEVDVCB0cywganNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQudm9sYXRpbGl0eV90aWVyJykgQVMgdm9sX3RpZXIsIFJPVU5EKGpzb25fZXh0cmFjdChwYXlsb2FkX2pzb24sJ1wkLmF0cl9wY3RfZDEnKSwzKSBBUyBhdHJfcGN0LCBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5zaXplX211bHRpcGxpZXInKSBBUyBzaXplX211bHQsIGpzb25fZXh0cmFjdChwYXlsb2FkX2pzb24sJ1wkLmhhcmRfemVyb19yZWFzb24nKSBBUyBoYXJkX3plcm8gRlJPTSBhdWRpdF9ldmVudCBXSEVSRSBraW5kPSdodGZfZ2F0ZV9kZWNpc2lvbicgQU5EIHRzPj0nJFcnIEFORCBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5hdHJfcGN0X2QxJyk+PTMuMCBBTkQganNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuYXRyX3BjdF9kMScpPDUuMCBPUkRFUiBCWSB0czsiCmVjaG8KCmVjaG8gIj09PSBTMWItRkFJTDogYW55IEFUUiBpbiBbMyw1KSBTVElMTCBjbGFzc2lmaWVkICdleHRyZW1lJyAvIGhhcmQtemVyb2VkIChNVVNUIGJlIDAgcm93cykgPT09IgpzcWxpdGUzIC1oZWFkZXIgLWNvbHVtbiAiJERCIiAiU0VMRUNUIENPVU5UKCopIEFTIHN0aWxsX2V4dHJlbWVfdW5kZXI1IEZST00gYXVkaXRfZXZlbnQgV0hFUkUga2luZD0naHRmX2dhdGVfZGVjaXNpb24nIEFORCB0cz49JyRXJyBBTkQganNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuYXRyX3BjdF9kMScpPj0zLjAgQU5EIGpzb25fZXh0cmFjdChwYXlsb2FkX2pzb24sJ1wkLmF0cl9wY3RfZDEnKTw1LjAgQU5EIChqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC52b2xhdGlsaXR5X3RpZXInKT0nZXh0cmVtZScgT1IganNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuaGFyZF96ZXJvX3JlYXNvbicpPSd2b2xfdGllcl9leHRyZW1lJyk7IgplY2hvCgplY2hvICI9PT0gUzFjOiBGSVJJTkcgUkVTVU1QVElPTiDigJQgYml0dW5peCB0cmFkZV9wbGFuX2RlY2lzaW9uIC8gd291bGRfaGF2ZV9wbGFjZWQgc2luY2Ugd2luZG93IHN0YXJ0ID09PSIKc3FsaXRlMyAtaGVhZGVyIC1jb2x1bW4gIiREQiIgIlNFTEVDVCBraW5kLCBDT1VOVCgqKSBBUyBuLCBNSU4odHMpIEFTIGZpcnN0LCBNQVgodHMpIEFTIGxhc3QgRlJPTSBhdWRpdF9ldmVudCBXSEVSRSBhY3Rvcj0nYml0dW5peF9mdXR1cmVzJyBBTkQga2luZCBJTiAoJ3RyYWRlX3BsYW5fZGVjaXNpb24nLCd3b3VsZF9oYXZlX3BsYWNlZCcpIEFORCB0cz49JyRXJyBHUk9VUCBCWSBraW5kIE9SREVSIEJZIGtpbmQ7IgplY2hvCgplY2hvICI9PT0gUzFkOiBQUklPUi1GSVJFIEJPVU5EQVJZIOKAlCBsYXN0IGJpdHVuaXggZmlyZSBCRUZPUkUgd2luZG93IChleHBlY3QgfjIwMjYtMDYtMDJUMjI6MTVaKSA9PT0iCnNxbGl0ZTMgLWhlYWRlciAtY29sdW1uICIkREIiICJTRUxFQ1Qga2luZCwgTUFYKHRzKSBBUyBsYXN0X2JlZm9yZV93aW5kb3cgRlJPTSBhdWRpdF9ldmVudCBXSEVSRSBhY3Rvcj0nYml0dW5peF9mdXR1cmVzJyBBTkQga2luZCBJTiAoJ3RyYWRlX3BsYW5fZGVjaXNpb24nLCd3b3VsZF9oYXZlX3BsYWNlZCcpIEFORCB0czwnJFcnIEdST1VQIEJZIGtpbmQ7IgplY2hvICI9PT0gRU5EIFN0ZXAgMSAocmVhZC1vbmx5KSA9PT0iCg== | base64 -d | bash'
```
Source script (for review): `reports/2026-06-09_fresh_window_eval/probe_step1_activation.sh`.

### Step 1 PASS / FAIL rubric
| Check | PASS condition | FAIL → action |
|---|---|---|
| **S1b** activation | ≥1 `htf_gate_decision` row with `atr_pct in [3,5)` shows `vol_tier=high`, `size_mult=1.0`, `hard_zero=NULL` | any such row still `extreme`/`vol_tier_extreme` |
| **S1b-FAIL** counter | `still_extreme_under5 = 0` | `> 0` → **fix did NOT take on prod** → STOP, re-verify prod md5 = `550609fad155da002ebb470a57e16709` + that restart loaded it |
| **S1c** firing resumed | `would_have_placed` and/or `trade_plan_decision` rows exist with `first >= 03:49:41Z` | both = 0 rows → see "no signal yet" below |
| **S1d** boundary | `last_before_window ≈ 2026-06-02T22:15Z` | materially different → flag (re-examine dormancy claim) |

- **No bitunix signal yet (S1a/S1c empty):** plausible this early — bitunix fires on TV-driven 1m signals only when score+PA+HTF all permit. `htf_gate_decision` rows (S1a) should appear well before `would_have_placed`. If **S1a is also empty**, the gate isn't even evaluating → that's a *different* anomaly (observer not seeing signals) → STOP and report, don't conflate with the vol fix. **Re-run Step 1 later** rather than widening assumptions.
- **Activation is confirmed by EITHER:** an S1b row in [3,5) classified `high` (direct proof), OR — if ATR has drifted outside [3,5) — `still_extreme_under5=0` plus S1a showing the live tier↔ATR mapping is sane. Record which.

---

## Step 2 — window evaluation (RUN ONLY AFTER Step 1 PASSES)

**Paste-safe one-liner:**
```
ssh azureuser@trading.jacksumner.com 'echo IyEvdXNyL2Jpbi9lbnYgYmFzaAojIFN0ZXAgMiDigJQgZnJlc2gtd2luZG93IGV2YWx1YXRpb24gcHJvYmUgKFJVTiBPTkxZIEFGVEVSIFN0ZXAgMSBhY3RpdmF0aW9uIFBBU1NFUykuCiMgRmlyZSByYXRlLCBvdXRjb21lcywgY2xhc3NpZmllciBzYW5pdHksIGFub21hbHkgc2NhbiBvdmVyIHRoZSBmcmVzaCBwYXBlciB3aW5kb3cKIyBzdGFydGluZyAyMDI2LTA2LTA5VDAzOjQ5OjQxWi4gUkVBRC1PTkxZLiBTRUxFQ1Qgb25seS4gTm8gd3JpdGVzLgpzZXQgLXVvIHBpcGVmYWlsCkRCPS9ob21lL2F6dXJldXNlci90cmFkaW5nX2NvcnAvZGF0YS90cmFkaW5nX2NvcnAuZGIKVz0nMjAyNi0wNi0wOVQwMzo0OTo0MSswMDowMCcKCmVjaG8gIj09PSBDT05URVhUID09PSIKZWNobyAiaG9zdD0kKGhvc3RuYW1lKSBub3dfdXRjPSQoZGF0ZSAtdSArJVktJW0tJWRUJUg6JU06JVNaKSB3aW5kb3dfc3RhcnQ9JFciCmVjaG8KCmVjaG8gIj09PSBTMmE6IEZJUkUgUkFURSDigJQgYml0dW5peCBwYXBlciBmaXJlcyBwZXIgZGF5IChwYXBlcl90cmFkZV9yZWNvcmQpLiBBbmNob3I6IHByZS1idWcgMDYtMDIgZmlyZWQgOXggPT09IgpzcWxpdGUzIC1oZWFkZXIgLWNvbHVtbiAiJERCIiAiU0VMRUNUIERBVEUodHMpIEFTIGRheSwgQ09VTlQoKikgQVMgZmlyZXMgRlJPTSBwYXBlcl90cmFkZV9yZWNvcmQgV0hFUkUgZGl2aXNpb249J2JpdHVuaXhfZnV0dXJlcycgQU5EIHRzPj0nJFcnIEdST1VQIEJZIGRheSBPUkRFUiBCWSBkYXk7IgplY2hvCgplY2hvICI9PT0gUzJiOiBPVVRDT01FUyDigJQgcmVzdWx0IGRpc3RyaWJ1dGlvbiArIGF2ZyBSICsgc3VtIFBuTCAoTlVMTCByZXN1bHQgPSBzdGlsbCBvcGVuL3VucmVzb2x2ZWQpID09PSIKc3FsaXRlMyAtaGVhZGVyIC1jb2x1bW4gIiREQiIgIlNFTEVDVCBDT0FMRVNDRShyZXN1bHQsJyh1bnJlc29sdmVkKScpIEFTIHJlc3VsdCwgQ09VTlQoKikgQVMgbiwgUk9VTkQoQVZHKGFjdHVhbF9yX211bHRpcGxlKSwzKSBBUyBhdmdfciwgUk9VTkQoU1VNKGFjdHVhbF9wbmxfZG9sbGFycyksMikgQVMgc3VtX3BubCBGUk9NIHBhcGVyX3RyYWRlX3JlY29yZCBXSEVSRSBkaXZpc2lvbj0nYml0dW5peF9mdXR1cmVzJyBBTkQgdHM+PSckVycgR1JPVVAgQlkgcmVzdWx0IE9SREVSIEJZIG4gREVTQzsiCmVjaG8KCmVjaG8gIj09PSBTMmM6IENMQVNTSUZJRVIgU0FOSVRZIOKAlCB2b2xfdGllciBkaXN0cmlidXRpb24gKyBBVFIgcmFuZ2UgYnkgZGF5ID09PSIKc3FsaXRlMyAtaGVhZGVyIC1jb2x1bW4gIiREQiIgIlNFTEVDVCBEQVRFKHRzKSBBUyBkYXksIGpzb25fZXh0cmFjdChwYXlsb2FkX2pzb24sJ1wkLnZvbGF0aWxpdHlfdGllcicpIEFTIHZvbF90aWVyLCBDT1VOVCgqKSBBUyBuLCBST1VORChNSU4oanNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuYXRyX3BjdF9kMScpKSwyKSBBUyBhdHJfbWluLCBST1VORChNQVgoanNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuYXRyX3BjdF9kMScpKSwyKSBBUyBhdHJfbWF4IEZST00gYXVkaXRfZXZlbnQgV0hFUkUga2luZD0naHRmX2dhdGVfZGVjaXNpb24nIEFORCB0cz49JyRXJyBHUk9VUCBCWSBkYXksIHZvbF90aWVyIE9SREVSIEJZIGRheSwgbiBERVNDOyIKZWNobwoKZWNobyAiPT09IFMyYy1GQUlMOiB0aWVyPC0+QVRSIGJhbmQgdmlvbGF0aW9ucyAoaGlnaCBNVVNUIGJlIFszLDUpOyBleHRyZW1lIE1VU1QgYmUgPj01KSDigJQgZXhwZWN0IDAgPT09IgpzcWxpdGUzIC1oZWFkZXIgLWNvbHVtbiAiJERCIiAiU0VMRUNUIENPVU5UKCopIEFTIGJhbmRfdmlvbGF0aW9ucyBGUk9NIGF1ZGl0X2V2ZW50IFdIRVJFIGtpbmQ9J2h0Zl9nYXRlX2RlY2lzaW9uJyBBTkQgdHM+PSckVycgQU5EICgoanNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQudm9sYXRpbGl0eV90aWVyJyk9J2V4dHJlbWUnIEFORCBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5hdHJfcGN0X2QxJyk8NS4wKSBPUiAoanNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQudm9sYXRpbGl0eV90aWVyJyk9J2hpZ2gnIEFORCAoanNvbl9leHRyYWN0KHBheWxvYWRfanNvbiwnXCQuYXRyX3BjdF9kMScpPDMuMCBPUiBqc29uX2V4dHJhY3QocGF5bG9hZF9qc29uLCdcJC5hdHJfcGN0X2QxJyk+PTUuMCkpKTsiCmVjaG8KCmVjaG8gIj09PSBTMmQ6IEhBUkQtU1RPUCDigJQgUGhhc2UgMyBsaXZlLW1vZGUgcHJpbWl0aXZlcyBmaXJpbmcgaW4gUEFQRVIgKEE1OyBNVVNUIGJlIDAgcm93cykgPT09IgpzcWxpdGUzIC1oZWFkZXIgLWNvbHVtbiAiJERCIiAiU0VMRUNUIGtpbmQsIENPVU5UKCopIEFTIGNvdW50LCBNSU4odHMpIEFTIGZpcnN0LCBNQVgodHMpIEFTIGxhc3QgRlJPTSBhdWRpdF9ldmVudCBXSEVSRSB0cz49JyRXJyBBTkQgKGtpbmQgTElLRSAnbGl2ZV9leGl0X29yZGVyXyUnIE9SIGtpbmQgTElLRSAncG9zaXRpb25fc3RhdGVfJScgT1Iga2luZCBMSUtFICdyZXN0YXJ0X3Jlc3VtZV8lJyBPUiBraW5kIElOICgnZXhpdF9vdXRjb21lX3JlY29yZGVkJywnb3JwaGFuX2Jyb2tlcl9wb3NpdGlvbl9vbl9yZXN0YXJ0JykpIEdST1VQIEJZIGtpbmQ7IgplY2hvCgplY2hvICI9PT0gUzJlOiBBTk9NQUxZIOKAlCBhZ2VudF9lcnJvciByb3dzIHNpbmNlIHdpbmRvdyBzdGFydCwgYnkgZGF5K2FjdG9yID09PSIKc3FsaXRlMyAtaGVhZGVyIC1jb2x1bW4gIiREQiIgIlNFTEVDVCBEQVRFKHRzKSBBUyBkYXksIGFjdG9yLCBDT1VOVCgqKSBBUyBuIEZST00gYXVkaXRfZXZlbnQgV0hFUkUga2luZD0nYWdlbnRfZXJyb3InIEFORCB0cz49JyRXJyBHUk9VUCBCWSBkYXksIGFjdG9yIE9SREVSIEJZIG4gREVTQyBMSU1JVCA0MDsiCmVjaG8KCmVjaG8gIj09PSBTMmY6IEFOT01BTFkg4oCUIHJlY29uY2lsZXItbWlzbWF0Y2ggLyBkaXZlcmdlbmNlIGtpbmRzIHNpbmNlIHdpbmRvdyBzdGFydCA9PT0iCnNxbGl0ZTMgLWhlYWRlciAtY29sdW1uICIkREIiICJTRUxFQ1Qga2luZCwgQ09VTlQoKikgQVMgbiwgTUFYKHRzKSBBUyBsYXN0IEZST00gYXVkaXRfZXZlbnQgV0hFUkUgdHM+PSckVycgQU5EIChraW5kIExJS0UgJyVyZWNvbmNpbCUnIE9SIGtpbmQgTElLRSAnJW1pc21hdGNoJScgT1Iga2luZCBMSUtFICclZGl2ZXJnZW5jZSUnKSBHUk9VUCBCWSBraW5kIE9SREVSIEJZIG4gREVTQzsiCmVjaG8gIj09PSBFTkQgU3RlcCAyIChyZWFkLW9ubHkpID09PSIK | base64 -d | bash'
```
Source script (for review): `reports/2026-06-09_fresh_window_eval/probe_step2_window.sh`.

### Step 2 interpretation anchors
- **S2a fire rate** — anchor: pre-bug 06-02 fired **9×/day** at ATR 2.92%. At ~4% ATR expect a *plausible* rate, **not a flood**. At window age <1 day, judge cumulative-to-date, not a daily extrapolation.
- **S2b outcomes** — most fires likely `(unresolved)`/`open` this early (TP/SL replay lags). Win-rate + R distribution mature over the window; **do not** form a readiness verdict on fire-rate alone.
- **S2c classifier sanity** — `high` band ATR range must sit in [3,5); `extreme` ≥5. **S2c-FAIL `band_violations` must be 0.**
- **S2d hard-stop** — **MUST be 0 rows.** Any Phase-3 live-mode primitive firing under paper is an A5-class hard-stop → STOP and report.
- **S2e/S2f anomalies** — note any `agent_error` spike vs baseline, or new reconciler-divergence rows. The known recurring external-feed WARNINGs (apify 403 / odds_api 401 / polymarket timeouts) are pre-existing, out-of-scope noise — don't conflate.

---

## Live-readiness read (to be completed once probe output returns)
*Template — filled after operator pastes Step 1 (+ Step 2) output.*
- **Activation:** PASS / FAIL — evidence: …
- **Firing resumed:** yes/no — first fire ts: …
- **Fire rate vs anchor:** …
- **Outcomes:** … (likely immature)
- **Classifier sanity / anomalies:** …
- **Read:** the window is/ is-not accumulating clean evidence toward a flip decision. **This is a readiness *signal*, not a flip authorization.**

### Decision gates before any paper→live flip (NONE satisfied by this window alone)
1. Clean fresh window of sufficient **duration** — prior practice 7 days → closes ~2026-06-16 03:49Z. **Operator sets length.**
2. Operator authorization **+ Backtester approval** (CLAUDE.md §4) — a live-flip is a higher bar than the wiring fix.
3. deploy_log 2026-06-02 `execution_mode` flip checklist: (a) operator auth, (b) reconcile-state review on open positions, (c) Path-C dry-run audit-row shape, (d) flip deploy-log entry (config-only; `strategies.yaml:1022` mtime-cached, no restart).
4. **Out of scope here:** I do not flip `execution_mode` or `auto_execute`.

## Open forks for the operator
- **Window duration** — not set; recommend ≥7 days. Operator decides close date.
- **P2 Robinhood re-login** (`b2259a0`, pickle stale since 05-29) — orthogonal to bitunix, but **any restart re-hits the ~22-min RH device-approval hang** until the pickle is regenerated. Doing the interactive re-login now pre-empts that hang for the next deploy. ~5 min, MFA, operator-manual.
- **P3 orphaned `high` threshold cleanup** (`4214c23`) — not gating; schedule whenever.

## Transport notes
- One-liners are LF-normalized base64 → `base64 -d | bash` on prod (CLAUDE.md §Environment pattern); single physical line, no continuations, no quoting hazards.
- If direct SSH is unavailable (NSG `temp-vpn-trip-until-2026-06-19` / travel), the `az vm run-command` `.cmd` wrappers in `reports/2026-06-08_bitunix_silence_investigation/` (investigation branch `10a8bfd`) are the fallback transport shape — same SQL.
