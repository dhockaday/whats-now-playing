#!/usr/bin/env python3
''' bootstrap the app '''

import logging
import logging.handlers
import os
import pathlib
import shutil
import sys
import time

from PySide2.QtCore import QCoreApplication, QSettings, QStandardPaths, Qt  # pylint: disable=no-name-in-module
import pkg_resources

import nowplaying.version


class Upgrade:
    ''' methods to upgrade from old configs to new configs '''
    def __init__(self):

        if sys.platform == "win32":
            self.qsettingsformat = QSettings.IniFormat
        else:
            self.qsettingsformat = QSettings.NativeFormat
        self.cparser = QSettings(self.qsettingsformat, QSettings.UserScope,
                                 QCoreApplication.organizationName(),
                                 QCoreApplication.applicationName())
        logging.info('configuration: %s', self.cparser.fileName())

        self.copy_old_2_new()
        self.upgrade()

    def backup_config(self):
        ''' back up the old config '''
        source = self.cparser.fileName()
        datestr = time.strftime("%Y%m%d-%H%M%S")
        backupdir = os.path.join(
            QStandardPaths.standardLocations(
                QStandardPaths.DocumentsLocation)[0],
            QCoreApplication.applicationName(), 'configbackup')

        logging.info('Making a backup of config prior to upgrade.')
        try:
            pathlib.Path(backupdir).mkdir(parents=True, exist_ok=True)
            backup = os.path.join(backupdir, f'{datestr}-config.bak')
            shutil.copyfile(source, backup)
        except Exception as error:  # pylint: disable=broad-except
            logging.error('Failed to make a backup: %s', error)
            sys.exit(0)

    def copy_old_2_new(self):
        ''' copy old config file name to new config file name '''
        if os.path.exists(self.cparser.fileName()):
            return

        othersettings = QSettings(self.qsettingsformat, QSettings.UserScope,
                                  'com.github.em1ran', 'NowPlaying')
        if not os.path.exists(othersettings.fileName()):
            return
        logging.debug(
            'Upgrading from old em1ran config to whatsnowplaying config')
        pathlib.Path(os.path.dirname(self.cparser.fileName())).mkdir(
            parents=True, exist_ok=True)
        shutil.copyfile(othersettings.fileName(), self.cparser.fileName())

    def upgrade(self):
        ''' variable re-mapping '''
        mapping = {
            'settings/interval': 'serato/interval',
            'settings/handler': 'settings/input'
        }
        source = self.cparser.fileName()

        if not os.path.exists(source):
            logging.debug('new install!')
            return

        try:
            oldversstr = self.cparser.value('settings/configversion',
                                            defaultValue='2.0.0')
        except TypeError:
            oldversstr = '2.0.0'

        thisverstr = nowplaying.version.get_versions()['version']
        oldversion = pkg_resources.parse_version(oldversstr)
        thisversion = pkg_resources.parse_version(thisverstr)

        logging.debug('versions %s vs %s', oldversion, thisverstr)

        if oldversion == thisversion:
            logging.debug('equivalent')
            return

        if oldversion > thisversion:
            logging.warning('Running an older version with a newer config...')
            return

        self.backup_config()

        logging.info('Upgrading config from %s to %s', oldversstr, thisverstr)
        for oldkey, newkey in mapping.items():
            try:
                newval = self.cparser.value(newkey)
            except:  # pylint: disable=bare-except
                pass

            if newval:
                logging.debug('%s has a value already: %s', newkey, newval)
                continue

            try:
                oldval = self.cparser.value(oldkey)
            except:  # pylint: disable=bare-except
                continue

            if oldval:
                logging.debug('Setting %s to %s', newkey, oldval)
                self.cparser.setValue(newkey, oldval)

        self.cparser.setValue('settings/configversion', thisverstr)
        self.cparser.sync()


def set_qt_names(app=None):
    ''' bootstrap Qt for configuration '''
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    if not app:
        app = QCoreApplication.instance()
    if not app:
        app = QCoreApplication()
    app.setOrganizationDomain('com.github.whatsnowplaying')
    app.setOrganizationName('whatsnowplaying')
    app.setApplicationName('NowPlaying')


def upgrade():
    ''' do an upgrade of an existing install '''
    logging.debug('Called upgrade')
    myupgrade = Upgrade()  #pylint: disable=unused-variable


def setuplogging(logpath=None):
    ''' configure logging '''
    besuretorotate = False

    if os.path.exists(logpath):
        besuretorotate = True

    logfhandler = logging.handlers.RotatingFileHandler(filename=logpath,
                                                       backupCount=10,
                                                       encoding='utf-8')
    if besuretorotate:
        logfhandler.doRollover()

    # this loglevel should eventually be tied into config
    # but for now, hard-set at info
    logging.basicConfig(
        format='%(asctime)s %(threadName)s %(module)s:%(funcName)s:%(lineno)d '
        + '%(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S%z',
        handlers=[logfhandler],
        level=logging.DEBUG)
    logging.info('starting up v%s',
                 nowplaying.version.get_versions()['version'])


def setuptemplates(bundledir=None, templatedir=None):
    ''' put the templates into place '''

    # first, define a method for copytree to use to
    # ignore files that already exist

    def bootstrap_template_ignore(srcdir, srclist):  # pylint: disable=unused-argument
        ''' do not copy template files that already exist '''
        ignore = []
        for src in srclist:
            check = os.path.join(templatedir, src)
            if os.path.exists(check):
                ignore.append(src)
            else:
                logging.debug('Adding %s to templates dir', src)

        return ignore

    # and now the main work...

    bundletemplatedir = os.path.join(bundledir, 'templates')

    if os.path.exists(bundletemplatedir):
        shutil.copytree(bundletemplatedir,
                        templatedir,
                        ignore=bootstrap_template_ignore,
                        dirs_exist_ok=True)
    else:
        logging.error('Cannot locate templates dir during bootstrap!')
