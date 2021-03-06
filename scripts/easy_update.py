#!/usr/bin/env python

import re
import os
import sys
import getopt
import imp
import json
import requests
import urllib2
try:
    import xmlrpclib
except ImportError:
    import xmlrpc.client as xmlrpclib

"""
   TODO
   convert to argparse, process flags first  (python_deps.py)
   add built in R packages to the <depend_exclude> list.
   Add --Py and -R flags to allow processing of other file types (example: azure-cli)
"""

class ExtsList(object):
    """ Extension List Update is a utilty program for maintaining EasyBuild
    easyconfig files for R, Python and Bioconductor.  R, Python and
    Bioconductor langueges support package extensions.  Easyconfig supports
    the building of the language with a list of extions. This program
    automates the the updating of extension lists for R and Python by using
    API for resolving current version for each package.

    command line arguments -> arguments
    --add [file] ->add_packages. add_packages is a list of package names to
          be added to exts_list.
    --check [package name] -> package.  If package is not None just check
            this one package and exit.

    Note: At the FredHutch BioCondutor packages are merged with R.
    An earlier version of easy_update built BioCondutor in a seperate package.

    Issues
       There are many small inconsistancies with PyPI which make it difficult
       to fully automate building of easyconfig files.
       - dependancy checking - check for extras=='all'
       - pypi projects names do not always match module names and or file names
         project: liac-arff, module: arff,  file_name: liac_arff.zip
    """
    def __init__(self, file_name, add_packages, package, verbose):
        self.add_packages = add_packages
        self.verbose = verbose
        self.checkpackage = False
        self.code = None
        self.ext_list_len = 0
        self.ext_counter = 0
        self.pkg_update = 0
        self.pkg_new = 0
        self.pkg_drop = 0

        self.exts_processed = []  # single list of package names
        self.depend_exclude = []  # built in packages not be added to exts_list
        self.prolog = '## remove ##\n'
        self.ptr_head = 0
        self.indent_n = 4
        self.indent = ' ' * self.indent_n
        self.pkg_top = None

        eb = self.parse_eb(file_name, primary=True)
        self.exts_orig = eb.exts_list
        self.toolchain = eb.toolchain
        self.dependencies = eb.dependencies
        self.version = eb.version
        self.name = eb.name
        self.pkg_name = eb.name + '-' + eb.version
        self.pkg_name += '-' + eb.toolchain['name']
        self.pkg_name += '-' + eb.toolchain['version']
        self.pkg_version = eb.version
        self.biocver = None 
        if self.pkg_name[0] == 'R':
            self.biocver = eb.biocver
        try:
            self.pkg_name += eb.versionsuffix
        except (AttributeError, NameError):
            pass
        f_name = os.path.basename(file_name)[:-3]
        if f_name != self.pkg_name:
            sys.stderr.write("Warning: file name does not match easybuild " +
                             "module name\n"),
            sys.stderr.write(" file name: %s, module name: %s\n" % (
                             f_name, self.pkg_name))
            sys.stderr.write('Writing output to: %s' % self.pkg_name +
                             '.update\n')

        # process command line arguments
        for pkg_name in add_packages:
            self.exts_orig.append((pkg_name, 'add'))
        if package:
            self.checkpackage = True
            self.exts_orig = [(package, 'add')]
        else:
            self.out = open(self.pkg_name + ".update", 'w')

    def parse_eb(self, file_name, primary):
        """ interpret easyconfig file with 'exec'.  Interperting fails if
        constants that are not defined within the easyconfig file.  Add
        undefined constants to <header>.
        """
        header = 'SOURCE_TGZ  = "%(name)s-%(version)s.tgz"\n'
        header += 'SOURCE_TAR_GZ = "%(name)s-%(version)s.tar.gz"\n'
        header += self.prolog
        code = header

        eb = imp.new_module("easyconfig")
        with open(file_name, "r") as f:
            code += f.read()
        try:
            exec (code, eb.__dict__)
        except Exception as err:
            print("interperting easyconfig error: %s" % err)
            sys.exit(1)
        if primary:     # save original text of source code
            self.code = code
            self.ptr_head = len(header)
        return eb

    def get_package_info(self, pkg):
        """base class 
        subclasses of <get_package_info> search CRAN, Pypi or Bioconductor
        based on easyconfig.
        input: pkg - list [pkg_name, pkg_version, source]
        return: [pkg_version, source, depends[]]
        <pkg_version> string; highest version of <pkg_name> available
        <source> Where to fine the package: PYPI, CRAN or Bioconductor  
        <depends> list of package names that are dependancies
        look hard for dependant packages, try to match different cases
        and interchange underscore and dash.  If input package_name
        does not match rewrite pkg[0] with name found from repository.
        """
        pass

    def is_processed(self, pkg_name):
        if pkg_name in [i[0] for i in self.exts_processed] or (
           pkg_name in self.depend_exclude):
            return True
        else:
            return False

    def check_package(self, pkg):
        """this is heart of the program
        check that <pkg> is the latest version.
        check that all dependancies are meet for each package.
        check that <pkg> is not a duplicate.

        input: pkg - list [pkg_name, pkg_version, source]
        check_package can be called recursivly. To track recursion
        global <self.pkg_top> contains the package name of the original
        call.
        """
        pkg_name = pkg[0]
        orig_ver = pkg[1]
        if self.is_processed(pkg_name):
            if pkg_name == self.pkg_top:
                pkg.append('duplicate')
                self.exts_processed.append(pkg)
            return
        if self.verbose > 1:
            print('check_package: (%s, %s)' % (pkg[0], pkg[1]))
        pkg_ver, pkg_source, depends = self.get_package_info(pkg)
        if pkg[0] != pkg_name:
            print("Repository package name does not match. %s -> %s" % (pkg_name, pkg[0]))
            if self.is_processed(pkg[0]):
                 return
        if pkg_ver == "error" or pkg_ver == 'not found':
            if pkg[0] == self.pkg_top and pkg[1][0:3] != 'add':
                pkg.append('keep')
            else:
                self.pkg_drop += 1
                print("Warning: package %s is " % pkg_name),
                print("a dependency of %s " % self.pkg_top),
                print("but can't be found!")
                return
        else:
            if self.pkg_top == pkg_name and pkg[1][0:3] != 'add':
                if pkg[1] == pkg_ver:
                    pkg.append('keep')
                else:
                    orig_ver = pkg[1]
                    pkg[1] = pkg_ver
                    pkg.append('update')
                    self.pkg_update += 1
            else:
                pkg[1] = pkg_ver
                if self.name == "Python":
                    ext_url = "{\n%s'source_urls': " % (self.indent * 2)
                    ext_url += "['https://pypi.python.org/packages/source/"
                    ext_url += "%s/%s/'],\n%s}" % (pkg_name[0], pkg_name,
                                                   self.indent)
                    pkg.append(ext_url)
                elif self.name == 'R':
                    pkg.append(pkg_source)
                    
                pkg.append('new')
                self.pkg_new += 1

        for depend in depends:
            if depend not in self.depend_exclude:
                self.check_package([depend, 'add-dep'])  # third arg?
        self.exts_processed.append(pkg)
        self.ext_counter += 1
        if self.verbose > 0:
            if len(pkg) < 4:
                print("Error:"),
            if pkg[-1] == 'update':
                version = '%s -> %s' % (orig_ver, pkg[1])
            elif pkg[-1][0:3] == 'add':
                version = '%s (%s)' % (pkg[1], pkg[-1])
            elif pkg[-1] == 'new':
                version = '%s (new)' % pkg[1]
            else:
                version = pkg[1]
            print("%20s : %-8s (%s) [%2d, %d]" % (pkg[0], pkg[1], pkg[-1],
                  self.ext_list_len, self.ext_counter))

    def update_exts(self):
        """
        """
        self.ext_list_len = len(self.exts_orig)
        for pkg in self.exts_orig:
            if isinstance(pkg, tuple):
                if self.verbose > 1:
                    print("update_exts loop package: %s" % pkg[0])
                self.pkg_top = pkg[0]
                self.check_package(list(pkg))
            else:
                self.exts_processed.append(pkg)
        #  Add new packages to EB file
        self.pkg_top = None
        for pkg_name in self.add_packages:
            self.check_package([pkg_name, 'add'])

    def write_chunk(self, indx):
        self.out.write(self.code[self.ptr_head:indx])
        self.ptr_head = indx

    def rewrite_extension(self, pkg):
        name_indx = self.code[self.ptr_head:].find(pkg[0])
        name_indx += self.ptr_head + len(pkg[0]) + 1
        indx = self.code[name_indx:].find("'") + name_indx + 1
        self.write_chunk(indx)
        self.out.write("%s'," % pkg[1])  # write version Number
        self.ptr_head = self.code[self.ptr_head:].find(',') + (
                        self.ptr_head + 1)
        indx = self.code[self.ptr_head:].find('),') + self.ptr_head + 3
        self.write_chunk(indx)

    def print_update(self):
        """ this needs to be re-written in a Pythonesque manor

        if check package [self.checkpackage] is set nothing needs to be written
        """
        if self.checkpackage:
            return
        indx = self.code.find('exts_list')
        indx += self.code[indx:].find('[')
        indx += self.code[indx:].find('\n') + 1
        self.write_chunk(indx)

        for extension in self.exts_processed:
            if isinstance(extension, str):  # base library with no version
                indx = self.code[self.ptr_head:].find(extension)
                indx += self.ptr_head + len(extension) + 2
                self.write_chunk(indx)
                continue
            action = extension.pop()
            if action == 'keep' or action == 'update':
                self.rewrite_extension(extension)
                # sys.exit(0)
            elif action == 'duplicate':
                print("duplicate: %s" % extension[0])
                name_indx = self.code[self.ptr_head:].find(extension[0])
                name_indx += self.ptr_head + len(extension[0])
                indx = self.code[name_indx:].find('),') + name_indx + 3
                self.ptr_head = indx
                continue
            elif action == 'new':
                self.out.write("%s('%s', '%s', %s),\n" % (self.indent,
                                                          extension[0],
                                                          extension[1],
                                                          extension[2]))
        self.out.write(self.code[self.ptr_head:])
        print("Updated Packages: %d" % self.pkg_update)
        print("New Packages: %d" % self.pkg_new)
        print("Dropped Packages: %d" % self.pkg_drop)


class R(ExtsList):
    """extend ExtsList class to update package names from CRAN
    """
    def __init__(self, file_name, add_packages, package, verbose):
        ExtsList.__init__(self, file_name, add_packages, package, verbose)
        self.bioc_data = {}
        self.depend_exclude = ['R', 'parallel', 'methods', 'utils', 'stats',
                               'stats4', 'graphics', 'grDevices', 'tools',
                               'tcltk', 'grid', 'splines', 'compiler',
                               'datasets']
        if self.biocver:
            self.read_bioconductor_pacakges()
        else:
            print('BioCondutor verserion: biocver not set')

    def read_bioconductor_pacakges(self):
        """ read the Bioconductor package list into bio_data dict
        """
        base_url = 'https://bioconductor.org/packages/json/%s' % self.biocver
        bioc_urls = ['%s/bioc/packages.json' % base_url,
                     '%s/data/annotation/packages.json' % base_url,
                     '%s/data/experiment/packages.json' % base_url]
        self.bioc_data = {}
        for url in bioc_urls:
            try:
                response = urllib2.urlopen(url)
            except IOError as e:
                print 'URL request: ', url
                sys.exit(e)
            self.bioc_data.update(json.loads(response.read()))


    def check_CRAN(self, pkg):
        cran_list = "http://crandb.r-pkg.org/"
        resp = requests.get(url=cran_list + pkg[0])

        cran_info = json.loads(resp.text)
        if 'error' in cran_info and cran_info['error'] == 'not_found':
            return "not found", []
        try:
            pkg_ver = cran_info[u'Version']
        except KeyError:
            return "error", []
        depends = []
        if u'License' in cran_info and u'Part of R' in cran_info[u'License']:
            return 'base package', []
        if u"Depends" in cran_info:
            depends = cran_info[u"Depends"].keys()
        if u"Imports" in cran_info:
            depends += cran_info[u"Imports"].keys()
        return pkg_ver, depends

    def check_BioC(self, pkg):
        """Extract <Depends> and <Imports> from BioCondutor json metadata
        Example:
        bioc_data['pkg']['Depends']
                    [u'R (>= 2.10)', u'BiocGenerics (>= 0.3.2)', u'utils']
        bioc_data['pkg']['Depends'] ['Imports'] [ 'Biobase', 'graphics']
        """
        depends = []
        if pkg[0] in self.bioc_data:
            pkg_ver = self.bioc_data[pkg[0]]['Version']
            if 'Depends' in self.bioc_data[pkg[0]]:
                depends = [re.split('[ (><=,]', s)[0]
                           for s in self.bioc_data[pkg[0]]['Depends']]
            if 'Imports' in self.bioc_data[pkg[0]]:
                depends = [re.split('[ (><=,]', s)[0]
                           for s in self.bioc_data[pkg[0]]['Imports']]
        else:
            pkg_ver = "not found"
        return pkg_ver, depends

    def print_depends(self, pkg, depends):
        for p in depends:
            if p not in self.depend_exclude:
                print("%20s : requires %s" % (pkg, p))

    def get_package_info(self, pkg):
        pkg_ver, depends = self.check_BioC(pkg)
        pkg_source = 'bioconductor_options'
        if pkg_ver == 'not found':
            pkg_ver, depends = self.check_CRAN(pkg)
            pkg_source = 'ext_options'
        if self.verbose > 0:
            for p in depends:
                print("%s requires: %s from %s" % (pkg[0], p, pkg_source))
        return pkg_ver, pkg_source, depends


class PythonExts(ExtsList):
    """extend ExtsList class to update package names from PyPI
    """
    def __init__(self, file_name, add_package, package, verbose):
        ExtsList.__init__(self, file_name, add_packages, package, verbose)
        self.verbose = verbose
        self.pkg_dict = None
        self.client = xmlrpclib.ServerProxy('https://pypi.python.org/pypi')
        (nums) = self.version.split('.')
        self.python_version = "%s.%s" % (nums[0], nums[1])

    def parse_pypi_requires(self, pkg_name, requires):
        """pip requirement specifier is defined in full in PEP 508
        The project name is the only required portion of a requirement string.

        Only install the latest version so ignore all version information
        input: 'numpy (>=1.7.1)'  output: 'numpy'

        Test that <python_version> and <sys_platform> conform.
        If <extra> is present and required check that extra is contained
        in "exts_list".
        wincertstore (==0.2); sys_platform=='win32' and extra == 'ssl'
        futures (>=3.0); (python_version=='2.7' or python_version=='2.6')
        requests-kerberos (>=0.6); extra == 'kerberos'
        trollius; python_version == "2.7" and extra == 'asyncio'
        asyncio; python_version == "3.3" and extra == 'asyncio'
        """
        sys_platform = 'Linux'
        python_version = self.python_version
        extra = ''
        platform_python_implementation = 'CPython'
        require_re = '^([A-Za-z0-9_\-\.]+)(?:.*)$'
        extra_re = "and\sextra\s==\s'([A-Za-z0-9_\-\.]+)'"  # only if the
        targets = ['python_version', 'sys_platform', 'extra']
        ans = re.search(require_re, requires)
        name = ans.group(1)
        state = True    # result of eval(requires)

        version = requires.split(';')
        if len(version) > 1:
            for target in targets:
                if target in version[1]:
                    # eval Example: python_version == "3.3" and sys_platform == 'Linux'
                    try:
                        state = eval(version[1])
                    except NameError:
                        print('Error: NameError on eval, ignoring')
                    except SyntaxError:
                        pass
                    if target == 'extra':
                        extra = re.search(extra_re, version[1])
                        extra = extra.group(1) if extra else None
                        if extra not in [i[0] for i in self.exts_processed]:
                            extra = None
        if state:
            if self.verbose > 1:
                if name not in [i[0] for i in self.exts_processed] and (
                   name not in self.depend_exclude):
                    print('Add dependent package: %s ' % name),
                    print('for: %s, Expression: %s' % (pkg_name, requires))
            return name
        else:
            if self.verbose > 1:
                print('Do not install: %s, ' % name +
                      'for package: %s, Expression: %s' % (pkg_name, requires))
            return None

    def get_package_info(self, pkg):
        """Python pypi API for package version and dependency list
           pkg is a list; ['package name', 'version', 'other stuff']
           return the version number for the package and a list of dependencies
        """
        depends = []
        (pkg_name, pkg_ver, xml_info, url_info) = get_pypi_info(pkg[0])
        pkg_source = 'PYPI'
        if not xml_info:
            print("Warning: %s Not in PyPi. " % pkg[0])
            print("No dependency checking performed")
            pkg_ver = 'not found'
            return pkg_ver, pkg_source, [] 
        URL = None
        for url in url_info:
            file_name = url['filename']
            if url['filename'].endswith('tar.gz'):
                URL = url['url']
                break
            if url['filename'].endswith('.zip'):
                URL = url['url']
                source_tmpl = url['filename'].replace(pkg_name, '%(name)s')
                source_tmpl = source_tmpl.replace(pkg_ver, '%(version)s')
            break
        # TODO look for whl
        if pkg[0] != pkg_name:
            pkg[0] = pkg_name
        if 'requires_dist' in xml_info.keys():
            for requires in xml_info['requires_dist']:
                pkg_requires = self.parse_pypi_requires(pkg_name, requires)
                if pkg_requires:
                    depends.append(pkg_requires)
        else:
            self.depend_exclude.append(pkg[0])
        return pkg_ver, depends

def get_pypi_info(pkg_name):
    """get version information from pypi.  If <pkg_name> is not found seach
    pypi. if <pkg_name> matches search results case; use the new value of
    pkg_name""" 
    client = xmlrpclib.ServerProxy('https://pypi.python.org/pypi')
    ver_list = client.package_releases(pkg_name)
    if len(ver_list) == 0:
        search_list = client.search({'name': pkg_name})
        for info in search_list:
            if pkg_name.lower() == info['name'].lower():
                 pkg_name = info['name']
                 break 
            if pkg_name.replace('-','_') == info['name'].lower() or (
               pkg_name.replace('_','-') == info['name'].lower() ):
                 pkg_name = info['name']
                 break
        ver_list = client.package_releases(pkg_name)
        if len(ver_list) == 0:
            return pkg_name, 'not found', {}, {} 
    version = ver_list[0]
    xml_info = client.release_data(pkg_name, version)
    url_info = client.release_urls(pkg_name, version)
    return pkg_name, version, xml_info, url_info

def help():
    print("usage: easy_update  easyconfig.eb [flags]")
    print("easy_update Updates ext_list information of EasyBuild"),
    print(" easyconfig files")
    print("easy_update works with R, Python and R-bioconductor"),
    print(" easyconfig files")
    print("  --verbose  diplay status for each package")
    print("  --add [filename]  filename contains list of package"),
    print(" names to add")


def get_package_list(fname, add_packages):
    """read package names from <fname>
    return list
    """
    with open(fname, "r") as pkg_file:
        for pkg in pkg_file:
            add_packages.append(pkg[:-1])


if __name__ == '__main__':
    if len(sys.argv) < 2:
        help()
        sys.exit(0)

    verbose = 0 
    add_packages = []
    package = None
    path = sys.argv[1]
    file_name = os.path.basename(path)
    myopts, args = getopt.getopt(sys.argv[2:], "",
                                 ['verbose',
                                  'add=',
                                  'check='])
    for opt, arg in myopts:
        if opt == "--add":
            get_package_list(arg, add_packages)
        elif opt == "--verbose":
            verbose += 1 
        elif opt == "--check":
            package = arg
    if file_name[:2] == 'R-':
#    def __init__(self, file_name, add_packages, package, verbose):
        module = R(path, add_packages, package, verbose)
    elif file_name[:7] == 'Python-':
        module = PythonExts(path, add_packages, package, verbose)
    else:
        print("Module name must begin with R- or Python-")
        sys.exit(1)
    module.update_exts()
    module.print_update()
