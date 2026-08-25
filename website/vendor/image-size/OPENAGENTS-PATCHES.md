# OpenAgents security patches

This package is built from the official `image-size` v2.0.2 tag with these
upstream security fixes applied:

- `bdbe560bfd98af6feab93b46aed67f2f0a77e4d5` fixes infinite loops in the
  HEIF and JXL parsers (GHSA-5p2g-fcmc-qvqq).
- `0f6a6665a166c530ba126a8ab8608a0603cb49dc` fixes the infinite loop in the
  ICNS parser (GHSA-w3rx-r6r6-pgpr).

The internal version `2.0.3-openagents.0` distinguishes this patched package
from the vulnerable upstream 2.0.2 release. Replace it with an official fixed
release once one is published.
