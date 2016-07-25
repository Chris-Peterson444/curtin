""" test_apt_source
    Collection of tests for the apt configuration features
"""
import textwrap

from . import VMBaseClass
from .releases import base_vm_classes as relbase

from unittest import SkipTest
from curtin import util


class TestAptSrcAbs(VMBaseClass):
    """TestAptSrcAbs - Basic tests for apt features of curtin"""
    interactive = False
    extra_disks = []
    fstab_expected = {}
    disk_to_check = []
    collect_scripts = [textwrap.dedent("""
        cd OUTPUT_COLLECT_D
        cat /etc/fstab > fstab
        ls /dev/disk/by-dname > ls_dname
        find /etc/network/interfaces.d > find_interfacesd
        apt-key list "F430BBA5" > keyid-F430BBA5
        apt-key list "03683F77" > keyppa-03683F77
        apt-key list "F470A0AC" > keylongid-F470A0AC
        apt-key list "8280B242" > keyraw-8280B242
        cp /etc/apt/sources.list.d/byobu-ppa.list .
        cp /etc/apt/sources.list.d/my-repo2.list .
        cp /etc/apt/sources.list.d/my-repo4.list .
        cp /etc/apt/sources.list.d/smoser-ubuntu-ppa-xenial.list .
        find /etc/apt/sources.list.d/ -maxdepth 1 -name "*ignore*" | wc -l > ic
        apt-config dump | grep Retries > aptconf
        cp /etc/apt/sources.list sources.list
        cp /etc/cloud/cloud.cfg.d/curtin-preserve-sources.cfg .
        """)]
    mirror = "http://us.archive.ubuntu.com/ubuntu"
    secmirror = "http://security.ubuntu.com/ubuntu"

    def test_output_files_exist(self):
        """test_output_files_exist - Check if all output files exist"""
        self.output_files_exist(
            ["fstab", "ic", "keyid-F430BBA5", "keylongid-F470A0AC",
             "keyraw-8280B242", "keyppa-03683F77", "aptconf", "sources.list",
             "byobu-ppa.list", "my-repo2.list", "my-repo4.list",
             "curtin-preserve-sources.cfg"])
        self.output_files_exist(
            ["smoser-ubuntu-ppa-%s.list" % self.release])

    def test_keys_imported(self):
        """test_keys_imported - Check if all keys are imported correctly"""
        self.check_file_regex("keyid-F430BBA5",
                              r"Launchpad PPA for Ubuntu Screen Profile")
        self.check_file_regex("keylongid-F470A0AC",
                              r"Ryan Harper")
        self.check_file_regex("keyppa-03683F77",
                              r"Launchpad PPA for Scott Moser")
        self.check_file_regex("keyraw-8280B242",
                              r"Christian Ehrhardt")

    def test_preserve_source(self):
        """test_preserve_source - no clobbering sources.list by cloud-init"""
        self.check_file_regex("curtin-preserve-sources.cfg",
                              "apt_preserve_sources_list.*true")

    def test_source_files(self):
        """test_source_files - Check generated .lists for correct content"""
        # hard coded deb lines
        self.check_file_strippedline("byobu-ppa.list",
                                     ("deb http://ppa.launchpad.net/byobu/"
                                      "ppa/ubuntu xenial main"))
        self.check_file_strippedline("my-repo4.list",
                                     ("deb http://ppa.launchpad.net/alestic/"
                                      "ppa/ubuntu xenial main"))
        # mirror and release replacement in deb line
        self.check_file_strippedline("my-repo2.list", "deb %s %s multiverse" %
                                     (self.mirror, self.release))
        # auto creation by apt-add-repository
        self.check_file_strippedline("smoser-ubuntu-ppa-%s.list" %
                                     self.release,
                                     ("deb http://ppa.launchpad.net/smoser/"
                                      "ppa/ubuntu %s main" % self.release))

    def test_ignore_count(self):
        """test_ignore_count - Check for files that should not be created"""
        self.check_file_strippedline("ic", "0")

    def test_apt_conf(self):
        """test_apt_conf - Check if the selected apt conf was set"""
        self.check_file_strippedline("aptconf", 'Acquire::Retries "3";')


class TestAptSrcCustom(TestAptSrcAbs):
    """TestAptSrcNormal - tests valid in the custom sources.list case"""
    conf_file = "examples/tests/apt_source_custom.yaml"

    def test_custom_source_list(self):
        """test_custom_source_list - Check custom sources with replacement"""
        # check that all replacements happened
        self.check_file_strippedline("sources.list",
                                     "deb %s %s main restricted" %
                                     (self.mirror, self.release))
        self.check_file_strippedline("sources.list",
                                     "deb-src %s %s main restricted" %
                                     (self.mirror, self.release))
        self.check_file_strippedline("sources.list",
                                     "deb %s %s universe restricted" %
                                     (self.mirror, self.release))
        self.check_file_strippedline("sources.list",
                                     "deb %s %s-security multiverse" %
                                     (self.secmirror, self.release))
        # check for something that guarantees us to come from our test
        self.check_file_strippedline("sources.list",
                                     "# nice line to check in test")


class TestAptSrcPreserve(TestAptSrcAbs):
    """TestAptSrcPreserve - tests valid in the preserved sources.list case"""
    conf_file = "examples/tests/apt_source_preserve.yaml"
    boot_cloudconf = None

    def test_preserved_source_list(self):
        """test_preserved_source_list - Check sources to be preserved as-is"""
        # curtin didn't touch it, so we should find what curtin set as default
        self.check_file_regex("sources.list",
                              r"this file is written by cloud-init")


class TestAptSrcModify(TestAptSrcAbs):
    """TestAptSrcModify - tests modifying sources.list"""
    conf_file = "examples/tests/apt_source_modify.yaml"

    def test_modified_source_list(self):
        """test_modified_source_list - Check sources with replacement"""
        # we set us.archive which is non default, check for that
        # this will catch if a target ever changes the expected defaults we
        # have to replace in case there is no custom template
        self.check_file_regex("sources.list",
                              r"us.archive.ubuntu.com")
        self.check_file_regex("sources.list",
                              r"security.ubuntu.com")


class TestAptSrcDisablePockets(TestAptSrcAbs):
    """TestAptSrcDisablePockets - tests disabling a suite in sources.list"""
    conf_file = "examples/tests/apt_source_modify_disable_suite.yaml"

    def test_disabled_suite(self):
        """test_disabled_suite - Check if suites were disabled"""
        # two not disabled
        self.check_file_regex("sources.list",
                              r"deb.*us.archive.ubuntu.com")
        self.check_file_regex("sources.list",
                              r"deb.*security.ubuntu.com")
        # updates disabled
        self.check_file_regex("sources.list",
                              r"# suite disabled by curtin:.*-updates")


class TestAptSrcModifyArches(TestAptSrcModify):
    """TestAptSrcModify - tests modifying sources.list with per arch mirror"""
    # same test, just different yaml to specify the mirrors per arch
    conf_file = "examples/tests/apt_source_modify_arches.yaml"


class TestAptSrcSearch(TestAptSrcAbs):
    """TestAptSrcSearch - tests checking a list of mirror options"""
    conf_file = "examples/tests/apt_source_search.yaml"

    def test_mirror_search(self):
        """test_mirror_search
           Check searching through a mirror list
           This is checked in the test (late) intentionally.
           No matter if resolution worked or failed it shouldn't fail
           fatally (python error and trace).
           We just can't rely on the content to be found in that case
           so we skip the check then."""
        res1 = util.is_resolvable_url("http://does.not.exist/ubuntu")
        res2 = util.is_resolvable_url("http://does.also.not.exist/ubuntu")
        res3 = util.is_resolvable_url("http://us.archive.ubuntu.com/ubuntu")
        res4 = util.is_resolvable_url("http://security.ubuntu.com/ubuntu")
        if res1 or res2 or not res3 or not res4:
            raise SkipTest(("Name resolution not as required"
                            "(%s, %s, %s, %s)" % (res1, res2, res3, res4)))

        self.check_file_regex("sources.list",
                              r"us.archive.ubuntu.com")
        self.check_file_regex("sources.list",
                              r"security.ubuntu.com")


class TestAptSrcSearchDNS(VMBaseClass):
    """TestAptSrcSearchDNS - tests checking for predefined DNS names"""
    interactive = False
    extra_disks = []
    fstab_expected = {}
    conf_file = "examples/tests/apt_source_search_dns.yaml"
    disk_to_check = []
    collect_scripts = [textwrap.dedent("""
        cd OUTPUT_COLLECT_D
        cat /etc/fstab > fstab
        ls /dev/disk/by-dname > ls_dname
        find /etc/network/interfaces.d > find_interfacesd
        cp /etc/apt/sources.list.d/dnssearch.list.disabled .
        """)]

    def test_output_files_exist(self):
        """test_output_files_exist - Check if all output files exist"""
        self.output_files_exist(["fstab", "dnssearch.list.disabled"])

    def test_mirror_search_dns(self):
        """test_mirror_search_dns - tests checking for predefined DNS names"""
        # these should be the first it got resolved, so they should be in the
        # sources.list file. We want to see that .lcoaldomain was not picked
        # but instead what we added to the temp /etc/hosts
        self.check_file_regex("dnssearch.list.disabled",
                              r"ubuntu-mirror/ubuntu.*multiverse")
        self.check_file_regex("dnssearch.list.disabled",
                              r"ubuntu-mirror/ubuntu.*universe")
        self.check_file_regex("dnssearch.list.disabled",
                              r"ubuntu-security-mirror/ubuntu.*main")


class XenialTestAptSrcCustom(relbase.xenial, TestAptSrcCustom):
    """ XenialTestAptSrcCustom
       Apt_source Test for Xenial with a custom template
    """
    __test__ = True


class XenialTestAptSrcPreserve(relbase.xenial, TestAptSrcPreserve):
    """ XenialTestAptSrcPreserve
       Apt_source Test for Xenial with apt_preserve_sources_list enabled
    """
    __test__ = True


class XenialTestAptSrcModify(relbase.xenial, TestAptSrcModify):
    """ XenialTestAptSrcModify
        Apt_source Test for Xenial modifying the sources.list of the image
    """
    __test__ = True


class XenialTestAptSrcSearch(relbase.xenial, TestAptSrcSearch):
    """ XenialTestAptSrcModify
        Apt_source Test for Xenial searching for mirrors
    """
    __test__ = True


class XenialTestAptSrcSearchDNS(relbase.xenial, TestAptSrcSearchDNS):
    """ XenialTestAptSrcModify
        Apt_source Test for Xenial searching for predefined DNS names
    """
    __test__ = True


class XenialTestAptSrcModifyArches(relbase.xenial, TestAptSrcModifyArches):
    """ XenialTestAptSrcModifyArches
        Apt_source Test for Xenial checking per arch mirror specification
    """
    __test__ = True


class XenialTestAptSrcDisablePockets(relbase.xenial, TestAptSrcDisablePockets):
    """ XenialTestAptSrcDisablePockets
        Apt_source Test for Xenial disabling a suite
    """
    __test__ = True
