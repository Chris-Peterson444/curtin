from .releases import base_vm_classes as relbase
from .test_bcache_basic import TestBcacheBasic


class TestBcacheBug1718699(TestBcacheBasic):
    conf_file = "examples/tests/bcache-wipe-xfs.yaml"
    dirty_disks = False
    nr_cpus = 2
    extra_disks = ['10G']


class PreciseTestBcacheBug1718699(relbase.precise_hwe_t, TestBcacheBug1718699):
    __test__ = True


class XenialTestBcacheBug1718699(relbase.xenial, TestBcacheBug1718699):
    __test__ = True


class ZestyTestBcacheBug1718699(relbase.zesty, TestBcacheBug1718699):
    __test__ = True


class ArtfulTestBcacheBug1718699(relbase.artful, TestBcacheBug1718699):
    __test__ = True
