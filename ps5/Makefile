# PS5 side of Game Shop. Needs john-tornblom's ps5-payload-sdk (Linux / WSL):
#   PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk make
# Builds the gameshop-open payload and the Game Shop tile app. Both are committed under gameshop/
# so the Windows build needs no SDK.
ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

OUT      := ../gameshop/payloads
CFLAGS   := -Wall -Werror -O1

all: $(OUT)/gameshop-open.elf app

$(OUT)/gameshop-open.elf: open/main.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -lSceSystemService -lSceUserService -o $@ $^

# ---------- the Game Shop tile: a PS5 app of its own ----------
# A folder Shadow Mount+ puts on the home screen when it sits in /data/homebrew/$(APP_ID)/
# (it only accepts IDs starting PPSA, CUSA, LAPY or FAKE). The payload SDK has no tools for apps;
# the native-app kit inside Forwarder Manager (https://github.com/heydemoura/ps5-forwarder-manager,
# GPL-3.0-or-later) has the linker layout, the converter that makes a fake-signed eboot.bin, and
# the clean-room libc.prx the system loader requires. Set it up once with FLEX's
# ps5-client/tools/setup-app-kit.sh (github.com/MrRobotto512/FLEX), into ~/ps5-app-kit.
APP_KIT  ?= $(HOME)/ps5-app-kit
APP_ID   ?= PPSA87500
APP_NAME ?= Game Shop
APP_OUT  := ../gameshop/ps5app/$(APP_ID)
APP_TOOL := $(APP_KIT)/build/host/ps5-native-tool
APP_SYS  := -lkernel -lSceLibcInternal -lSceSystemService -lSceUserService
BUILD    := build

app: $(APP_OUT)/eboot.bin

$(BUILD)/app/main.o: app/main.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -DTITLE_ID='"$(APP_ID)"' -c -o $@ $<

$(BUILD)/app/pie.elf: $(BUILD)/app/main.o app/symbols.map
	$(LD) -T $(APP_KIT)/tooling/native/ps5-pie.ld --version-script app/symbols.map -e _start -o $@ \
	    $(BUILD)/app/main.o -L$(PS5_SYSROOT)/lib --as-needed $(APP_SYS)

$(APP_OUT)/eboot.bin: $(BUILD)/app/pie.elf app/param.json ../gameshop/web/tile-icon.png
	$(APP_TOOL) link --in $< --out $(BUILD)/app/eboot.elf --stub-dir $(PS5_SYSROOT)/lib \
	    --module-sdk 0x02000009 --companion-sdk 0x08050001 --file-name eboot.elf
	rm -rf $(APP_OUT)
	mkdir -p $(APP_OUT)/sce_module $(APP_OUT)/sce_sys
	$(APP_TOOL) self --sign --in $(BUILD)/app/eboot.elf --out $@ --magic 0x1D3D154F
	cp $(APP_KIT)/runtime/libc.prx $(APP_OUT)/sce_module/
	sed 's/@ID@/$(APP_ID)/g; s/@CONCEPT@/$(patsubst PPSA%,%,$(APP_ID))/; s/@NAME@/$(APP_NAME)/' \
	    app/param.json > $(APP_OUT)/sce_sys/param.json
	cp ../gameshop/web/tile-icon.png $(APP_OUT)/sce_sys/icon0.png

clean:
	rm -rf $(BUILD) $(OUT)/gameshop-open.elf $(APP_OUT)

.PHONY: all app clean
