# Test glossary

Fixture for `tests/test_fixterms.py`. Deliberately **not** your real `glossary.md`:
tests must not depend on private data, and they must behave the same on a fresh clone.

The invented product names below stand in for the kind of term ASR mangles. The
third-party names (FLIR, Lepton, DGX Spark, Tailscale, LLM, Hewlett Packard, ArgoCD)
are real and kept, because their real-world mishearings are what make this test
meaningful.

## Do NOT auto-correct these — measured false positives

| Looks like | Actually |
|---|---|
| "insight" | the ordinary word, not the product name Insite |
| "pilot" | the ordinary word, not Copilot |
| "cluster", "master" | ordinary words, not "caster" |

## Likely ASR mishearings

| Heard as | Almost certainly |
|---|---|
| "Blue Falcon", "Bloo Falcon", "Bluefalken" | **BlueFalcon** |
| "Laptone", "leptin" | **Lepton** |
| "laptondan" | **Lepton'dan** |
| "flör", "flirt" | **FLIR** |
| "flörden" | **FLIR'den** |
| "DJX Spark", "DJX Park" | **DGX Spark** |
| "DJX parka" | **DGX Spark'a** |
| "mini 640" | **Mini640** |
| "lazer scan" | **laser scan** |
| "argo cd" | **ArgoCD** |
| "North wind" | **Northwind** |
| "ladlenin" | **ladle'nin** |
| "Telsic", "teliskel", "Telscale", "Telescale" | **Tailscale** |
| "Telsic'e" | **Tailscale'e** |
| "elelem", "el elem" | **LLM** |
| "elelemle", "el elemle" | **LLM'le** |
| "elelemler", "el elemler" | **LLM'ler** |
| "Helmut Peckert'in", "Helmut Beckert'ın" | **Hewlett Packard'ın** |
| "Hal Packard'ın" | **Hewlett Packard'ın** |
| "Halut Packard" | **Hewlett Packard** |
| "pulpit ekibi / pulpit ekibiyle" | **ambiguous, must be skipped** |
