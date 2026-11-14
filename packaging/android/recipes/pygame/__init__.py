"""A newer pygame than python-for-android ships.

p4a's bundled pygame recipe is pinned to 2.1.0 (October 2021), whose C sources
`#include <longintrepr.h>` -- a header CPython moved into `Include/cpython/` in
3.11 and that no supported CPython now exposes at the top level. The result is a
hard compile failure about forty minutes into a cold build:

    src_c/_sdl2/sdl2.c:211:12: fatal error: 'longintrepr.h' file not found

Pinning CPython back to 3.10 to match would work, but it ties the app to an
end-of-life interpreter and to a pygame four years older than the one the game
is developed and tested against. Overriding the version forward is the better
trade: it is a one-field change, and pygame's Android build template
(`buildconfig/Setup.Android.SDL2.in`) still takes exactly the placeholders the
upstream recipe fills -- verified identical between 2.1.0 and 2.6.1 -- so none
of p4a's build logic needs touching.

Keep this version in step with `requirements.txt`, so the device runs the same
pygame the tests ran against.
"""
from pythonforandroid.recipes.pygame import Pygame2Recipe


class FeatherDashPygameRecipe(Pygame2Recipe):
    version = "2.6.1"
    url = "https://github.com/pygame/pygame/archive/{version}.tar.gz"

    # pygame 2.1.0's extensions were plain C; from 2.2 onward several of them
    # (the _sdl2 package in particular) are Cython sources compiled at build
    # time. p4a installs this list into the *host* interpreter it uses to drive
    # the cross-compile, and the upstream recipe only asks for setuptools, so
    # without Cython here the build stops with
    #     Compiling Cython files
    #     You need cython. https://cython.org/
    # Pinned below 3.1: pygame 2.6.1 generates its C with the 0.29/3.0 API.
    hostpython_prerequisites = ["setuptools", "Cython<3.1"]


recipe = FeatherDashPygameRecipe()
