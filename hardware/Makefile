# Screen Hopper triple-Pico carrier PCB - build entrypoint.
# Requires: KiCad 10 (for pcbnew + kicad-cli) and a JRE (for the freerouting autorouter).
# Drop freerouting.jar into lib/ (see lib/README or hardware/README.md).

KICAD := /Applications/KiCad/KiCad.app/Contents
PY    := $(KICAD)/Frameworks/Python.framework/Versions/Current/bin/python3
CLI   := $(KICAD)/MacOS/kicad-cli

.PHONY: all smd tht compact header unplaced check clean
all: smd tht compact header  ## build, route, verify and export all variants

smd:                  ## compact SMD, fab-assembled variant
	bash lib/pipeline.sh smd

tht:                  ## through-hole, self-assembled variant
	bash lib/pipeline.sh tht

compact:              ## double-sided SMD (Pico A/B stacked, USB-C + USB-A host)
	bash lib/pipeline.sh compact

header:               ## two-row plug-in 0.1" header Picos (USB-C only, J3 USB tap)
	bash lib/pipeline.sh header

unplaced:             ## footprints + netlist only, no placement/routing (for external auto-placers)
	mkdir -p unplaced
	$(PY) lib/build_board.py unplaced smd unplaced/screen-hopper-smd-unplaced.kicad_pcb
	$(PY) lib/build_board.py unplaced tht unplaced/screen-hopper-tht-unplaced.kicad_pcb
	$(PY) lib/gen_sch.py smd unplaced/screen-hopper-smd-unplaced.kicad_sch
	$(PY) lib/gen_sch.py tht unplaced/screen-hopper-tht-unplaced.kicad_sch

check:                ## re-verify the already-built boards (connectivity/clearance/isolation) and schematics
	$(PY) lib/check_board.py smd/screen-hopper-smd.kicad_pcb 65.03 68.97
	$(PY) lib/check_board.py tht/screen-hopper-tht.kicad_pcb 68.69 75.31
	$(PY) lib/check_board.py compact/screen-hopper-compact.kicad_pcb 49.02 52.98 0.15
	$(PY) lib/check_board.py header/screen-hopper-header.kicad_pcb 34.73 38.67 0.15 y
	$(PY) lib/check_sch.py smd/screen-hopper-smd.kicad_sch
	$(PY) lib/check_sch.py tht/screen-hopper-tht.kicad_sch
	$(PY) lib/check_sch.py compact/screen-hopper-compact.kicad_sch
	$(PY) lib/check_sch.py header/screen-hopper-header.kicad_sch

clean:                ## remove generated boards and fab outputs (regenerate with make all)
	rm -rf smd tht compact header unplaced
