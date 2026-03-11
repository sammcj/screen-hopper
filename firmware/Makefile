.PHONY: all pico1 pico2 clean submodules

all: pico1 pico2

submodules:
	@git -C .. submodule update --init --recursive

pico1: submodules
	@mkdir -p build-pico1
	@cd build-pico1 && cmake .. && $(MAKE)

pico2: submodules
	@mkdir -p build-pico2
	@cd build-pico2 && cmake -DPICO_BOARD=pico2 .. && $(MAKE)

clean:
	@rm -rf build-pico1 build-pico2 pico1 pico2
