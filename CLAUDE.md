# Screen Hopper Hardware Reference

## 6N137 Optocoupler Pinout (DIP-8)

Datasheet: `/Users/samm/Downloads/6N137.PDF`

```
      dot/notch
  NC  [1]   [8] VCC
   A  [2]   [7] VE (enable, active low, has internal pull-down)
   C  [3]   [6] VO (output, active low, open collector)
  NC  [4]   [5] GND
```

- Pin 1: NC (no connection)
- Pin 2: Anode (IR LED input, positive)
- Pin 3: Cathode (IR LED input, negative/ground side)
- Pin 4: NC (no connection)
- Pin 5: GND (output side ground)
- Pin 6: VO (output, active low open collector, needs pull-up)
- Pin 7: VE (enable, active low, internally pulled low = enabled by default)
- Pin 8: VCC (output side power, 4.5V-5.5V)

## PCB Wiring (Triple Pico Version)

### Pico A to Optocoupler (TX side)

- Pico A 3V3 -> 470 ohm resistor -> Pin 2 (Anode)
- Pico A GPIO20 -> Pin 3 (Cathode)

When GPIO20 goes LOW (UART transmitting), current flows from 3V3 through
the resistor, through the IR LED (Anode to Cathode), to GPIO20. This
activates the optocoupler.

### Forwarder to Optocoupler (RX side)

- Forwarder VBUS -> Pin 8 (VCC)
- Forwarder GND -> Pin 5 (GND)
- Forwarder 3V3 -> 680 ohm resistor -> Pin 6 (VO)
- Forwarder GPIO9 -> Pin 6 (VO)

The 680 ohm resistor acts as a pull-up for the open collector output.
When the IR LED activates, VO pulls low, which the forwarder reads on GPIO9.

### Pico A to Pico B (Serial Link)

- Pico A GPIO0 (UART0 TX) -> Pico B GPIO1 (UART0 RX)
- Pico A GPIO1 (UART0 RX) -> Pico B GPIO0 (UART0 TX)
- Pico A GPIO2 (UART0 CTS) -> Pico B GPIO3 (UART0 RTS)
- Pico A GPIO3 (UART0 RTS) -> Pico B GPIO2 (UART0 CTS)
- VBUS shared, GND shared

## Firmware UART Configuration

- UART0 (serial.cc): A-to-B link, 4 Mbaud, GPIO0-3, hardware flow control
- UART1 (remapper.cc): A-to-forwarder link, 1 Mbaud, GPIO20 TX only
- UART1 (forwarder.cc): Forwarder RX, 1 Mbaud, GPIO9 RX only
