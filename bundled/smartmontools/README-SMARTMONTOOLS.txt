smartctl (smartmontools 7.5, 64-bit Windows build) + drivedb.h
===============================================================
These two files are the unmodified official release from the smartmontools project,
taken from smartmontools-7.5.win32-setup.exe (md5 bb1e199ad6a3db3e1c27ae54b835cbd5):
    https://github.com/smartmontools/smartmontools/releases/tag/RELEASE_7_5
smartmontools is Copyright (C) Bruce Allen, Christian Franke and contributors and is licensed
under the GNU General Public License, version 2 or later (see COPYING.txt next to this file).
Complete corresponding source code:
    https://github.com/smartmontools/smartmontools/releases/download/RELEASE_7_5/smartmontools-7.5.tar.gz
    https://www.smartmontools.org/

DriveDoctor runs smartctl as a separate program (a command line call that returns JSON). DriveDoctor
itself is a separate work and is not a derivative of smartmontools. DriveDoctor only ever uses these
read-only smartctl calls:   smartctl --scan -j     and     smartctl -a -j -d <type> <device>
plus, only when you click the labeled button and confirm:   smartctl -t short -d <type> <device>
