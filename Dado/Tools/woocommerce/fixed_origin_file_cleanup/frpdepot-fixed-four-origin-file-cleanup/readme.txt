=== FRP Depot Fixed Four Origin File Cleanup ===
Contributors: frpdepot
Tags: one-use, maintenance
Requires at least: 6.0
Requires PHP: 7.4
Stable tag: 1.0.0

A fixed one-use plugin commissioned only for four exact unregistered August 2026
origin files. Activation first verifies every fixed path, byte count, SHA-256,
fixed attachment-ID absence, and exact _wp_attached_file-record absence. Only
after all four preflights pass does it unlink the files sequentially. It then
schedules its own deactivation for the same request and emits only a bounded
activation-result marker.

There is no settings page, public route, REST route, AJAX route, admin-post
route, arbitrary path, upload, product/order/customer write, email, retry,
rollback, restore, or generic cleanup capability. The separately commissioned
named Python tool is responsible for immutable staging, one-attempt activation,
public/record verification, and deletion of only this inactive plugin after full
success.
