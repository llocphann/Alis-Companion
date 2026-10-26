/* Test-only virtual pointer. The driver supplies a distinct owned compositor.
 * No /dev/input, host socket, privileges or real desktop input is accessed. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>
#include "pointer-protocol.h"

static struct zwlr_virtual_pointer_manager_v1 *manager;
static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    if (!strcmp(interface, "zwlr_virtual_pointer_manager_v1"))
        manager=wl_registry_bind(registry,name,&zwlr_virtual_pointer_manager_v1_interface,1);
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {}
static const struct wl_registry_listener listener={global,removed};
static uint32_t now(void) {
    struct timespec ts;clock_gettime(CLOCK_MONOTONIC,&ts);
    return (uint32_t)(ts.tv_sec*1000+ts.tv_nsec/1000000);
}
int main(void) {
    const char *display_name=getenv("WAYLAND_DISPLAY"),*parent=getenv("POCKET_PARENT_WAYLAND");
    const char *owned=getenv("POCKET_PRIVATE_WAYLAND");
    if (!display_name || !parent || !owned || strcmp(display_name,parent)==0
            || strcmp(display_name,owned)!=0) return 2;
    struct wl_display *display=wl_display_connect(display_name);
    if (!display) return 3;
    struct wl_registry *registry=wl_display_get_registry(display);
    wl_registry_add_listener(registry,&listener,NULL);wl_display_roundtrip(display);
    if (!manager) return 77;
    struct zwlr_virtual_pointer_v1 *pointer=zwlr_virtual_pointer_manager_v1_create_virtual_pointer(manager,NULL);
    wl_display_roundtrip(display);setbuf(stdout,NULL);puts("POCKET_POINTER_READY");
    char line[96],op;unsigned a,b;
    while (fgets(line,sizeof(line),stdin)) {
        int count=sscanf(line,"%c %u %u",&op,&a,&b);
        if (op=='q') break;
        if (op=='m' && count==3 && a<=10000 && b<=10000)
            zwlr_virtual_pointer_v1_motion_absolute(pointer,now(),a,b,10000,10000);
        else if (op=='b' && count==2 && a<=1)
            zwlr_virtual_pointer_v1_button(pointer,now(),0x110,a);
        else return 4;
        zwlr_virtual_pointer_v1_frame(pointer);
        if (wl_display_roundtrip(display)<0) return 5;
        puts("POCKET_POINTER_APPLIED");
    }
    zwlr_virtual_pointer_v1_destroy(pointer);
    zwlr_virtual_pointer_manager_v1_destroy(manager);
    wl_display_flush(display);wl_display_disconnect(display);return 0;
}
