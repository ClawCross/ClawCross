Upstream: https://github.com/X1aoZhe/OpusMC
Revision: ca677140a47e668aeb2cfab65d4b3057f786f25d

Source code is MIT, copyright (c) 2026 Jeff; see LICENSE.
The upstream game/js/assets.js is deliberately excluded. All rendered resources
in the default pack are generated locally by our original procedural-assets.js.
No Minecraft images, GUI textures, fonts or logos are bundled. An optional, separate
host-private test file may be selected in settings by the room owner for their own
view. It is not covered by MIT. The save adapter uses the host backend.

Local modification: screens.js labels the selected pack accurately. Adapter files
outside this vendor directory provide settings, input and member-owned host saves.
