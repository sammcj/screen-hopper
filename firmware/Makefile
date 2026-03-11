.PHONY: all pico1 pico2 clean submodules bake flash apply-config get-config check-versions

all: pico2

BAKED_HEADER := src/baked_config.h

# Guard against the silent-drop failure where a host tool's CONFIG_VERSION lags
# the firmware's (the device rejects the report but the host still reports
# success). Fails the build listing any stale tool. See check_protocol_version.py.
check-versions:
	@python3 ../config-tool/check_protocol_version.py

submodules:
	@git -C .. submodule update --init --recursive

# Generate the baked-in default config header. Pass CONFIG=path/to/config.json
# to bake that config in as the firmware's default (loaded when flash holds no
# persisted config, e.g. straight after flashing). With no CONFIG, the existing
# header is left untouched so a previously baked config stays sticky across
# plain rebuilds; only a missing header is regenerated empty. Use
# `make bake CONFIG=` (empty) to clear a baked config.
bake:
ifneq ($(origin CONFIG),undefined)
	@python3 ../config-tool/bake_config.py $(if $(CONFIG),"$(CONFIG)",--empty) > $(BAKED_HEADER)
	@echo "Wrote $(BAKED_HEADER) ($(if $(CONFIG),baked $(CONFIG),no baked config))"
else
	@test -f $(BAKED_HEADER) || python3 ../config-tool/bake_config.py --empty > $(BAKED_HEADER)
endif

pico1: submodules check-versions bake
	@mkdir -p build-pico1
	@cd build-pico1 && cmake .. && $(MAKE)

pico2: submodules check-versions bake
	@mkdir -p build-pico2
	@cd build-pico2 && cmake -DPICO_BOARD=pico2 .. && $(MAKE)

clean:
	@rm -rf build-pico1 build-pico2 pico1 pico2

# Flash a built image to the connected Pico. Builds first (so any CONFIG=
# passed gets baked in), then tries the BOOTSEL volume, falling back to the
# Raspberry Pi Debug Probe over SWD.
#   make flash TARGET=a                                          # uses current baked config
#   make flash TARGET=a CONFIG=../config-tool/config-samm.json   # re-bakes then flashes
#   make flash TARGET=forwarder                                  # flashes forwarder
#   make flash TARGET=a BOARD=pico1                              # use pico1 artifacts
# TARGET: a | b | forwarder | single
# BOARD:  pico1 | pico2 (default: pico2)
BOARD ?= pico2
flash: $(BOARD)
ifndef TARGET
	@echo "Usage: make flash TARGET=<a|b|forwarder|single> [BOARD=pico1|pico2] [CONFIG=path]" >&2; exit 2
else
	@./scripts/flash.sh "$(TARGET)" "$(BOARD)" "$(CONFIG)"
endif

# Push a config to the running Pico A via USB HID AND re-bake it into the
# firmware header so the next `make flash` carries the same default. Does not
# flash; no reboot needed. The rebake makes the new config sticky - next time
# you `make pico2` or `make flash`, the UF2 will pick it up automatically.
# Accepts either single-profile or multi-profile JSON (see config-tool/
# config-samm-profiles.json for the multi-profile shape).
#   make apply-config CONFIG=../config-tool/config-samm-profiles.json
apply-config:
ifndef CONFIG
	@echo "Usage: make apply-config CONFIG=path/to/config.json" >&2; exit 2
else
	@python3 ../config-tool/bake_config.py "$(CONFIG)" > $(BAKED_HEADER)
	@echo "Baked $(CONFIG) into $(BAKED_HEADER) (will apply on next flash)"
	@python3 ../config-tool/set_config.py < "$(CONFIG)"
	@echo "Applied to running Pico A."
endif

# Dump the live config from the running Pico A over USB HID. Useful for
# verifying which screens/mappings the firmware actually has loaded.
#   make get-config
get-config:
	@python3 ../config-tool/get_config.py
