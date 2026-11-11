# PS5 payloads for Game Shop. Needs john-tornblom's ps5-payload-sdk (Linux / WSL):
#   PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk make
# The built ELFs are committed under gameshop/payloads/ so the Windows build needs no SDK.
ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

OUT      := ../gameshop/payloads
TITLE_ID := GMSH00001
CFLAGS   := -Wall -Werror -O1 -DTITLE_ID=\"$(TITLE_ID)\"

all: $(OUT)/gameshop-tile.elf $(OUT)/gameshop-open.elf

$(OUT)/gameshop-tile.elf: tile/main.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -lSceAppInstUtil -o $@ $^

$(OUT)/gameshop-open.elf: open/main.c
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -lSceSystemService -lSceUserService -o $@ $^

clean:
	rm -f $(OUT)/gameshop-tile.elf $(OUT)/gameshop-open.elf
