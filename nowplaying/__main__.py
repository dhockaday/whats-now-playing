#!/usr/bin/env python3
''' NowPlaying as run via python -m '''
#import faulthandler
import logging
import multiprocessing
import os
import socket
import sys

from PySide6.QtCore import QCoreApplication, Qt  # pylint: disable=no-name-in-module
from PySide6.QtGui import QIcon  # pylint: disable=no-name-in-module
from PySide6.QtWidgets import QApplication  # pylint: disable=no-name-in-module

import nowplaying
import nowplaying.bootstrap
import nowplaying.config
import nowplaying.db
import nowplaying.systemtray
import nowplaying.upgrade

# pragma: no cover
#
# as of now, there isn't really much here to test... basic bootstrap stuff
#


def run_bootstrap(bundledir=None):
    ''' bootstrap the app '''
    # we are in a hurry to get results.  If it takes longer than
    # 5 seconds, consider it a failure and move on.  At some
    # point this should be configurable but this is good enough for now
    socket.setdefaulttimeout(5.0)
    logpath = nowplaying.bootstrap.setuplogging(rotate=True)
    logging.info('starting up v%s',
                 nowplaying.version.get_versions()['version'])
    nowplaying.upgrade.upgrade(bundledir=bundledir)

    # fail early if metadatadb can't be configured
    metadb = nowplaying.db.MetadataDB()
    metadb.setupsql()
    return logpath


def main():
    ''' main entrypoint '''

    multiprocessing.freeze_support()
    #faulthandler.enable()

    # set paths for bundled files
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        bundledir = getattr(sys, '_MEIPASS',
                            os.path.abspath(os.path.dirname(__file__)))
    else:
        bundledir = os.path.abspath(os.path.dirname(__file__))

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    nowplaying.bootstrap.set_qt_names()
    logpath = run_bootstrap(bundledir=bundledir)

    config = nowplaying.config.ConfigFile(logpath=logpath, bundledir=bundledir)
    logging.getLogger().setLevel(config.loglevel)
    logging.captureWarnings(True)
    tray = nowplaying.systemtray.Tray()  # pylint: disable=unused-variable
    icon = QIcon(str(config.iconfile))
    qapp.setWindowIcon(icon)
    exitval = qapp.exec_()
    logging.info('shutting down v%s',
                 nowplaying.version.get_versions()['version'])
    sys.exit(exitval)


if __name__ == '__main__':
    main()
