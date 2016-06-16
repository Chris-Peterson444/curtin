#   Copyright (C) 2013 Canonical Ltd.
#
#   Author: Scott Moser <scott.moser@canonical.com>
#
#   Curtin is free software: you can redistribute it and/or modify it under
#   the terms of the GNU Affero General Public License as published by the
#   Free Software Foundation, either version 3 of the License, or (at your
#   option) any later version.
#
#   Curtin is distributed in the hope that it will be useful, but WITHOUT ANY
#   WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
#   FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for
#   more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with Curtin.  If not, see <http://www.gnu.org/licenses/>.

import errno
import os
import stat
import shlex
import tempfile
import itertools

from curtin import util
from curtin.udev import udevadm_settle
from curtin.log import LOG


def get_dev_name_entry(devname):
    bname = devname.split('/dev/')[-1]
    return (bname, "/dev/" + bname)


def is_valid_device(devname):
    devent = get_dev_name_entry(devname)[1]
    return is_block_device(devent)


def is_block_device(path):
    try:
        return stat.S_ISBLK(os.stat(path).st_mode)
    except OSError as e:
        if not util.is_file_not_found_exc(e):
            raise
    return False


def dev_short(devname):
    if os.path.sep in devname:
        return os.path.basename(devname)
    return devname


def dev_path(devname):
    if devname.startswith('/dev/'):
        return devname
    else:
        return '/dev/' + devname


def sys_block_path(devname, add=None, strict=True):
    toks = ['/sys/class/block']
    # insert parent dev if devname is partition
    (parent, partnum) = get_blockdev_for_partition(devname)
    if partnum:
        toks.append(dev_short(parent))

    toks.append(dev_short(devname))

    if add is not None:
        toks.append(add)
    path = os.sep.join(toks)

    if strict and not os.path.exists(path):
        err = OSError(
            "devname '{}' did not have existing syspath '{}'".format(
                devname, path))
        err.errno = errno.ENOENT
        raise err

    return os.path.normpath(path)


def _lsblock_pairs_to_dict(lines):
    ret = {}
    for line in lines.splitlines():
        toks = shlex.split(line)
        cur = {}
        for tok in toks:
            k, v = tok.split("=", 1)
            cur[k] = v
        # use KNAME, as NAME may include spaces and other info,
        # for example, lvm decices may show 'dm0 lvm1'
        cur['device_path'] = get_dev_name_entry(cur['KNAME'])[1]
        ret[cur['KNAME']] = cur
    return ret


def _lsblock(args=None):
    # lsblk  --help | sed -n '/Available/,/^$/p' |
    #     sed -e 1d -e '$d' -e 's,^[ ]\+,,' -e 's, .*,,' | sort
    keys = ['ALIGNMENT', 'DISC-ALN', 'DISC-GRAN', 'DISC-MAX', 'DISC-ZERO',
            'FSTYPE', 'GROUP', 'KNAME', 'LABEL', 'LOG-SEC', 'MAJ:MIN',
            'MIN-IO', 'MODE', 'MODEL', 'MOUNTPOINT', 'NAME', 'OPT-IO', 'OWNER',
            'PHY-SEC', 'RM', 'RO', 'ROTA', 'RQ-SIZE', 'SCHED', 'SIZE', 'STATE',
            'TYPE', 'UUID']
    if args is None:
        args = []
    args = [x.replace('!', '/') for x in args]

    # in order to avoid a very odd error with '-o' and all output fields above
    # we just drop one.  doesn't really matter which one.
    keys.remove('SCHED')
    basecmd = ['lsblk', '--noheadings', '--bytes', '--pairs',
               '--output=' + ','.join(keys)]
    (out, _err) = util.subp(basecmd + list(args), capture=True)
    out = out.replace('!', '/')
    return _lsblock_pairs_to_dict(out)


def get_unused_blockdev_info():
    # return a list of unused block devices. These are devices that
    # do not have anything mounted on them.

    # get a list of top level block devices, then iterate over it to get
    # devices dependent on those.  If the lsblk call for that specific
    # call has nothing 'MOUNTED", then this is an unused block device
    bdinfo = _lsblock(['--nodeps'])
    unused = {}
    for devname, data in bdinfo.items():
        cur = _lsblock([data['device_path']])
        mountpoints = [x for x in cur if cur[x].get('MOUNTPOINT')]
        if len(mountpoints) == 0:
            unused[devname] = data
    return unused


def get_devices_for_mp(mountpoint):
    # return a list of devices (full paths) used by the provided mountpoint
    bdinfo = _lsblock()
    found = set()
    for devname, data in bdinfo.items():
        if data['MOUNTPOINT'] == mountpoint:
            found.add(data['device_path'])

    if found:
        return list(found)

    # for some reason, on some systems, lsblk does not list mountpoint
    # for devices that are mounted.  This happens on /dev/vdc1 during a run
    # using tools/launch.
    mountpoint = [os.path.realpath(dev)
                  for (dev, mp, vfs, opts, freq, passno) in
                  get_proc_mounts() if mp == mountpoint]

    return mountpoint


def get_installable_blockdevs(include_removable=False, min_size=1024**3):
    good = []
    unused = get_unused_blockdev_info()
    for devname, data in unused.items():
        if not include_removable and data.get('RM') == "1":
            continue
        if data.get('RO') != "0" or data.get('TYPE') != "disk":
            continue
        if min_size is not None and int(data.get('SIZE', '0')) < min_size:
            continue
        good.append(devname)
    return good


def get_blockdev_for_partition(devpath):
    # convert an entry in /dev/ to parent disk and partition number
    # if devpath is a block device and not a partition, return (devpath, None)

    # input of /dev/vdb or /dev/disk/by-label/foo
    # rpath is hopefully a real-ish path in /dev (vda, sdb..)
    rpath = os.path.realpath(devpath)

    bname = os.path.basename(rpath)
    syspath = "/sys/class/block/%s" % bname

    if not os.path.exists(syspath):
        syspath2 = "/sys/class/block/cciss!%s" % bname
        if not os.path.exists(syspath2):
            raise ValueError("%s had no syspath (%s)" % (devpath, syspath))
        syspath = syspath2

    ptpath = os.path.join(syspath, "partition")
    if not os.path.exists(ptpath):
        return (rpath, None)

    ptnum = util.load_file(ptpath).rstrip()

    # for a partition, real syspath is something like:
    # /sys/devices/pci0000:00/0000:00:04.0/virtio1/block/vda/vda1
    rsyspath = os.path.realpath(syspath)
    disksyspath = os.path.dirname(rsyspath)

    diskmajmin = util.load_file(os.path.join(disksyspath, "dev")).rstrip()
    diskdevpath = os.path.realpath("/dev/block/%s" % diskmajmin)

    # diskdevpath has something like 253:0
    # and udev has put links in /dev/block/253:0 to the device name in /dev/
    return (diskdevpath, ptnum)


def get_pardevs_on_blockdevs(devs):
    # return a dict of partitions with their info that are on provided devs
    if devs is None:
        devs = []
    devs = [get_dev_name_entry(d)[1] for d in devs]
    found = _lsblock(devs)
    ret = {}
    for short in found:
        if found[short]['device_path'] not in devs:
            ret[short] = found[short]
    return ret


def stop_all_unused_multipath_devices():
    """
    Stop all unused multipath devices.
    """
    multipath = util.which('multipath')

    # Command multipath is not available only when multipath-tools package
    # is not installed. Nothing needs to be done in this case because system
    # doesn't create multipath devices without this package installed and we
    # have nothing to stop.
    if not multipath:
        return

    # Command multipath -F flushes all unused multipath device maps
    cmd = [multipath, '-F']
    try:
        # unless multipath cleared *everything* it will exit with 1
        util.subp(cmd, rcs=[0, 1])
    except util.ProcessExecutionError as e:
        LOG.warn("Failed to stop multipath devices: %s", e)


def rescan_block_devices():
    # run 'blockdev --rereadpt' for all block devices not currently mounted
    unused = get_unused_blockdev_info()
    devices = []
    for devname, data in unused.items():
        if data.get('RM') == "1":
            continue
        if data.get('RO') != "0" or data.get('TYPE') != "disk":
            continue
        devices.append(data['device_path'])

    if not devices:
        LOG.debug("no devices found to rescan")
        return

    cmd = ['blockdev', '--rereadpt'] + devices
    try:
        util.subp(cmd, capture=True)
    except util.ProcessExecutionError as e:
        # FIXME: its less than ideal to swallow this error, but until
        # we fix LP: #1489521 we kind of need to.
        LOG.warn("rescanning devices failed: %s", e)

    udevadm_settle()

    return


def blkid(devs=None, cache=True):
    if devs is None:
        devs = []

    # 14.04 blkid reads undocumented /dev/.blkid.tab
    # man pages mention /run/blkid.tab and /etc/blkid.tab
    if not cache:
        cfiles = ("/run/blkid/blkid.tab", "/dev/.blkid.tab", "/etc/blkid.tab")
        for cachefile in cfiles:
            if os.path.exists(cachefile):
                os.unlink(cachefile)

    cmd = ['blkid', '-o', 'full']
    # blkid output is <device_path>: KEY=VALUE
    # where KEY is TYPE, UUID, PARTUUID, LABEL
    out, err = util.subp(cmd, capture=True)
    data = {}
    for line in out.splitlines():
        curdev, curdata = line.split(":", 1)
        data[curdev] = dict(tok.split('=', 1) for tok in shlex.split(curdata))
    return data


def detect_multipath(target_mountpoint):
    """
    Detect if the operating system has been installed to a multipath device.
    """
    # The obvious way to detect multipath is to use multipath utility which is
    # provided by the multipath-tools package. Unfortunately, multipath-tools
    # package is not available in all ephemeral images hence we can't use it.
    # Another reasonable way to detect multipath is to look for two (or more)
    # devices with the same World Wide Name (WWN) which can be fetched using
    # scsi_id utility. This way doesn't work as well because WWNs are not
    # unique in some cases which leads to false positives which may prevent
    # system from booting (see LP: #1463046 for details).
    # Taking into account all the issues mentioned above, curent implementation
    # detects multipath by looking for a filesystem with the same UUID
    # as the target device. It relies on the fact that all alternative routes
    # to the same disk observe identical partition information including UUID.
    # There are some issues with this approach as well though. We won't detect
    # multipath disk if it doesn't any filesystems.  Good news is that
    # target disk will always have a filesystem because curtin creates them
    # while installing the system.
    rescan_block_devices()
    binfo = blkid(cache=False)
    LOG.debug("detect_multipath found blkid info: %s", binfo)
    # get_devices_for_mp may return multiple devices by design. It is not yet
    # implemented but it should return multiple devices when installer creates
    # separate disk partitions for / and /boot. We need to do UUID-based
    # multipath detection against each of target devices.
    target_devs = get_devices_for_mp(target_mountpoint)
    LOG.debug("target_devs: %s" % target_devs)
    for devpath, data in binfo.items():
        # We need to figure out UUID of the target device first
        if devpath not in target_devs:
            continue
        # This entry contains information about one of target devices
        target_uuid = data.get('UUID')
        # UUID-based multipath detection won't work if target partition
        # doesn't have UUID assigned
        if not target_uuid:
            LOG.warn("Target partition %s doesn't have UUID assigned",
                     devpath)
            continue
        LOG.debug("%s: %s" % (devpath, data.get('UUID', "")))
        # Iterating over available devices to see if any other device
        # has the same UUID as the target device. If such device exists
        # we probably installed the system to the multipath device.
        for other_devpath, other_data in binfo.items():
            if ((other_data.get('UUID') == target_uuid) and
                    (other_devpath != devpath)):
                return True
    # No other devices have the same UUID as the target devices.
    # We probably installed the system to the non-multipath device.
    return False


def get_scsi_wwid(device, replace_whitespace=False):
    """
    Issue a call to scsi_id utility to get WWID of the device.
    """
    cmd = ['/lib/udev/scsi_id', '--whitelisted', '--device=%s' % device]
    if replace_whitespace:
        cmd.append('--replace-whitespace')
    try:
        (out, err) = util.subp(cmd, capture=True)
        scsi_wwid = out.rstrip('\n')
        return scsi_wwid
    except util.ProcessExecutionError as e:
        LOG.warn("Failed to get WWID: %s", e)
        return None


def get_multipath_wwids():
    """
    Get WWIDs of all multipath devices available in the system.
    """
    multipath_devices = set()
    multipath_wwids = set()
    devuuids = [(d, i['UUID']) for d, i in blkid().items() if 'UUID' in i]
    # Looking for two disks which contain filesystems with the same UUID.
    for (dev1, uuid1), (dev2, uuid2) in itertools.combinations(devuuids, 2):
        if uuid1 == uuid2:
            multipath_devices.add(get_blockdev_for_partition(dev1)[0])
    for device in multipath_devices:
        wwid = get_scsi_wwid(device)
        # Function get_scsi_wwid() may return None in case of errors or
        # WWID field may be empty for some buggy disk. We don't want to
        # propagate both of these value further to avoid generation of
        # incorrect /etc/multipath/bindings file.
        if wwid:
            multipath_wwids.add(wwid)
    return multipath_wwids


def get_root_device(dev, fpath="curtin"):
    """
    Get root partition for specified device, based on presence of /curtin.
    """
    partitions = get_pardevs_on_blockdevs(dev)
    target = None
    tmp_mount = tempfile.mkdtemp()
    for i in partitions:
        dev_path = partitions[i]['device_path']
        mp = None
        try:
            util.do_mount(dev_path, tmp_mount)
            mp = tmp_mount
            curtin_dir = os.path.join(tmp_mount, fpath)
            if os.path.isdir(curtin_dir):
                target = dev_path
                break
        except:
            pass
        finally:
            if mp:
                util.do_umount(mp)

    os.rmdir(tmp_mount)

    if target is None:
        raise ValueError("Could not find root device")
    return target


def get_blockdev_sector_size(devpath):
    """
    Get the logical and physical sector size of device at devpath
    Returns a tuple of integer values (logical, physical).
    """
    info = _lsblock([devpath])
    LOG.debug('get_blockdev_sector_size: info:\n%s' % util.json_dumps(info))
    [parent] = info
    return (int(info[parent]['LOG-SEC']), int(info[parent]['PHY-SEC']))


def get_volume_uuid(path):
    """
    Get uuid of disk with given path. This address uniquely identifies
    the device and remains consistant across reboots
    """
    (out, _err) = util.subp(["blkid", "-o", "export", path], capture=True)
    for line in out.splitlines():
        if "UUID" in line:
            return line.split('=')[-1]
    return ''


def get_mountpoints():
    """
    Returns a list of all mountpoints where filesystems are currently mounted.
    """
    info = _lsblock()
    proc_mounts = [mp for (dev, mp, vfs, opts, freq, passno) in
                   get_proc_mounts()]
    lsblock_mounts = list(i.get("MOUNTPOINT") for name, i in info.items() if
                          i.get("MOUNTPOINT") is not None and
                          i.get("MOUNTPOINT") != "")

    return list(set(proc_mounts + lsblock_mounts))


def get_proc_mounts():
    """
    Returns a list of tuples for each entry in /proc/mounts
    """
    mounts = []
    with open("/proc/mounts", "r") as fp:
        for line in fp:
            try:
                (dev, mp, vfs, opts, freq, passno) = \
                    line.strip().split(None, 5)
                mounts.append((dev, mp, vfs, opts, freq, passno))
            except ValueError:
                continue
    return mounts


def lookup_disk(serial):
    """
    Search for a disk by its serial number using /dev/disk/by-id/
    """
    # Get all volumes in /dev/disk/by-id/ containing the serial string. The
    # string specified can be either in the short or long serial format
    disks = list(filter(lambda x: serial in x, os.listdir("/dev/disk/by-id/")))
    if not disks or len(disks) < 1:
        raise ValueError("no disk with serial '%s' found" % serial)

    # Sort by length and take the shortest path name, as the longer path names
    # will be the partitions on the disk. Then use os.path.realpath to
    # determine the path to the block device in /dev/
    disks.sort(key=lambda x: len(x))
    path = os.path.realpath("/dev/disk/by-id/%s" % disks[0])

    if not os.path.exists(path):
        raise ValueError("path '%s' to block device for disk with serial '%s' \
            does not exist" % (path, serial))
    return path


def sysfs_partition_data(blockdev=None, sysfs_path=None):
    # given block device or sysfs_path, return a list of tuples
    # of (kernel_name, number, offset, size)
    if blockdev is None and sysfs_path is None:
        raise ValueError("Blockdev and sysfs_path cannot both be None")

    if blockdev:
        sysfs_path = sys_block_path(blockdev)

    ptdata = []
    # /sys/class/block/dev has entries of 'kname' for each partition

    # queue property is only on parent devices, ie, we can't read
    # /sys/class/block/vda/vda1/queue/* as queue is only on the
    # parent device
    (parent, partnum) = get_blockdev_for_partition(blockdev)
    sysfs_prefix = sysfs_path
    if partnum:
        sysfs_prefix = sys_block_path(parent)

    block_size = int(util.load_file(os.path.join(sysfs_prefix,
                                    'queue/logical_block_size')))

    block_size = int(
        util.load_file(os.path.join(sysfs_path, 'queue/logical_block_size')))
    unit = block_size
    for d in os.listdir(sysfs_path):
        partd = os.path.join(sysfs_path, d)
        data = {}
        for sfile in ('partition', 'start', 'size'):
            dfile = os.path.join(partd, sfile)
            if not os.path.isfile(dfile):
                continue
            data[sfile] = int(util.load_file(dfile))
        if 'partition' not in data:
            continue
        ptdata.append((d, data['partition'], data['start'] * unit,
                       data['size'] * unit,))

    return ptdata


def wipe_file(path, reader=None, buflen=4 * 1024 * 1024):
    # wipe the existing file at path.
    #  if reader is provided, it will be called as a 'reader(buflen)'
    #  to provide data for each write.  Otherwise, zeros are used.
    #  writes will be done in size of buflen.
    if reader:
        readfunc = reader
    else:
        buf = buflen * b'\0'

        def readfunc(size):
            return buf

    with open(path, "rb+") as fp:
        # get the size by seeking to end.
        fp.seek(0, 2)
        size = fp.tell()
        LOG.debug("%s is %s bytes. wiping with buflen=%s",
                  path, size, buflen)
        fp.seek(0)
        while True:
            pbuf = readfunc(buflen)
            pos = fp.tell()
            if len(pbuf) != buflen and len(pbuf) + pos < size:
                raise ValueError(
                    "short read on reader got %d expected %d after %d" %
                    (len(pbuf), buflen, pos))

            if pos + buflen >= size:
                fp.write(pbuf[0:size-pos])
                break
            else:
                fp.write(pbuf)


def quick_zero(path, partitions=True):
    # zero 1M at front, 1M at end, and 1M at front
    # if this is a block device and partitions is true, then
    # zero 1M at front and end of each partition.
    buflen = 1024
    count = 1024
    zero_size = buflen * count
    offsets = [0, -zero_size]
    is_block = is_block_device(path)
    if not (is_block or os.path.isfile(path)):
        raise ValueError("%s: not an existing file or block device")

    if partitions and is_block:
        ptdata = sysfs_partition_data(path)
        for kname, ptnum, start, size in ptdata:
            offsets.append(start)
            offsets.append(start + size - zero_size)

    LOG.debug("wiping 1M on %s at offsets %s", path, offsets)
    return zero_file_at_offsets(path, offsets, buflen=buflen, count=count)


def zero_file_at_offsets(path, offsets, buflen=1024, count=1024, strict=False):
    bmsg = "{path} (size={size}): "
    m_short = bmsg + "{tot} bytes from {offset} > size."
    m_badoff = bmsg + "invalid offset {offset}."
    if not strict:
        m_short += " Shortened to {wsize} bytes."
        m_badoff += " Skipping."

    buf = b'\0' * buflen
    tot = buflen * count
    msg_vals = {'path': path, 'tot': buflen * count}
    with open(path, "rb+") as fp:
        # get the size by seeking to end.
        fp.seek(0, 2)
        size = fp.tell()
        msg_vals['size'] = size

        for offset in offsets:
            if offset < 0:
                pos = size + offset
            else:
                pos = offset
            msg_vals['offset'] = offset
            msg_vals['pos'] = pos
            if pos > size or pos < 0:
                if strict:
                    raise ValueError(m_badoff.format(**msg_vals))
                else:
                    LOG.debug(m_badoff.format(**msg_vals))
                    continue

            msg_vals['wsize'] = size - pos
            if pos + tot > size:
                if strict:
                    raise ValueError(m_short.format(**msg_vals))
                else:
                    LOG.debug(m_short.format(**msg_vals))
            fp.seek(pos)
            for i in range(count):
                pos = fp.tell()
                if pos + buflen > size:
                    fp.write(buf[0:size-pos])
                else:
                    fp.write(buf)


def wipe_volume(path, mode="superblock"):
    """wipe a volume/block device

    :param path: a path to a block device
    :param mode: how to wipe it.
       pvremove: wipe a lvm physical volume
       zero: write zeros to the entire volume
       random: write random data (/dev/urandom) to the entire volume
       superblock: zero the beginning and the end of the volume
       superblock-recursive: zero the beginning of the volume, the end of the
                    volume and beginning and end of any partitions that are
                    known to be on this device.
    """
    if mode == "pvremove":
        # We need to use --force --force in case it's already in a volgroup and
        # pvremove doesn't want to remove it

        # If pvremove is run and there is no label on the system,
        # then it exits with 5. That is also okay, because we might be
        # wiping something that is already blank
        cmds = []
        cmds.append(["pvremove", "--force", "--force", "--yes", path])

        # the lvm tools lvscan, vgscan and pvscan on ubuntu precise do not
        # support the flag --cache. the flag is present for the tools in ubuntu
        # trusty and later. since lvmetad is used in current releases of
        # ubuntu, the --cache flag is needed to ensure that the data cached by
        # lvmetad is updated.

        # if we are unable to determine the version of ubuntu we are running
        # on, we are much more likely to be correct in using the --cache flag,
        # as every supported release except for precise supports the --cache
        # flag, and most releases being installed now are either trusty or
        # xenial
        release_code_str = util.lsb_release().get('release')
        if release_code_str is None or release_code_str == 'UNAVAILABLE':
            LOG.warn('unable to find release number, assuming trusty or later')
            release_code_str = '14.04'
        release_code = float(release_code_str)

        for cmd in [['pvscan'], ['vgscan', '--mknodes']]:
            if release_code >= 14.04:
                cmd.append('--cache')
            cmds.append(cmd)

        for cmd in cmds:
            util.subp(cmd, rcs=[0, 5], capture=True)

    elif mode == "zero":
        wipe_file(path)
    elif mode == "random":
        with open("/dev/urandom", "rb") as reader:
            wipe_file(path, reader=reader.read)
    elif mode == "superblock":
        quick_zero(path, partitions=False)
    elif mode == "superblock-recursive":
        quick_zero(path, partitions=True)
    else:
        raise ValueError("wipe mode %s not supported" % mode)

# vi: ts=4 expandtab syntax=python
