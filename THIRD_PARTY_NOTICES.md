# Third-party notices — Windows acceptance candidate

This candidate uses the repository's existing Windows CPython 3.10.8 runtime recipe. The actual interpreter reports OpenSSL 1.1.1q and SQLite 3.37.2. These are the versions tested for this candidate; this is not a claim that they are current upstream releases. Aether's MIT license does not replace third-party terms.

| Component | Bundled terms and provenance |
| --- | --- |
| CPython 3.10.8, standard library and its binary extension dependencies | `runtime/LICENSE.txt` retains the Python distribution's complete supplied license and bundled notices, including Microsoft Distributable Code restrictions, OpenSSL/SSLeay, compression and other components. |
| Jinja2 3.1.6 | `runtime/Lib/site-packages/jinja2-3.1.6.dist-info/licenses/LICENSE.txt`: Copyright 2007 Pallets; BSD 3-Clause terms. |
| MarkupSafe 2.1.3 | `runtime/Lib/site-packages/MarkupSafe-2.1.3.dist-info/LICENSE.rst`: Copyright 2010 Pallets; BSD 3-Clause terms. |
| SQLite 3.37.2 | Supplied with the Python runtime; SQLite is dedicated to the public domain. |

The bundled Python terms state: This product includes software developed by the OpenSSL Project for use in the OpenSSL Toolkit (http://www.openssl.org/). They also retain the original SSLeay terms and attribution to Eric Young.

`bundle.json` lists each runtime and application file with its SHA-256. The build receipt retains the exact standard-library input/archive mapping. Runtime byte provenance and local qualification are distinct from public publication approval; this archive is supplied for installed acceptance.
