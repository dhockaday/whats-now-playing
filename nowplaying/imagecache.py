#!/usr/bin/env python3
# pylint: disable=invalid-name
''' image cache '''

import concurrent.futures
import pathlib
import random
import sqlite3
import threading
import time
import uuid

import logging
import logging.config
import logging.handlers

import diskcache
import requests_cache

from PySide6.QtCore import QStandardPaths  # pylint: disable=no-name-in-module

import nowplaying.bootstrap
import nowplaying.utils
import nowplaying.version

TABLEDEF = '''
CREATE TABLE artistsha
(url TEXT PRIMARY KEY,
 cachekey TEXT DEFAULT NULL,
 artist TEXT NOT NULL,
 strikes INT DEFAULT 0,
 imagetype TEXT NOT NULL,
 timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
 );
'''

MAX_FANART_DOWNLOADS = 50


class ImageCache:
    ''' database operations for caches '''

    def __init__(self, sizelimit=1, initialize=False, cachedir=None):
        if not cachedir:
            self.cachedir = pathlib.Path(
                QStandardPaths.standardLocations(
                    QStandardPaths.CacheLocation)[0]).joinpath('imagecache')

        else:
            self.cachedir = pathlib.Path(cachedir)

        self.cachedir.resolve().mkdir(parents=True, exist_ok=True)
        self.databasefile = self.cachedir.joinpath('imagecache.db')
        if not self.databasefile.exists():
            initialize = True
        self.httpcachefile = self.cachedir.joinpath('http')
        self.cache = diskcache.Cache(
            directory=self.cachedir.joinpath('diskcache'),
            eviction_policy='least-frequently-used',
            size_limit=sizelimit * 1024 * 1024 * 1024)
        if initialize:
            logging.debug('initialize imagecache')
            self.cache.clear()
            self.setup_sql(initialize=True)
        self.session = None
        self.logpath = None

    def setup_sql(self, initialize=False):
        ''' create the database '''

        if initialize and self.databasefile.exists():
            self.databasefile.unlink()

        if self.databasefile.exists():
            return

        logging.info('Create imagecache db file %s', self.databasefile)
        self.databasefile.resolve().parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.databasefile) as connection:

            cursor = connection.cursor()

            try:
                cursor.execute(TABLEDEF)
            except sqlite3.OperationalError:
                cursor.execute('DROP TABLE artistsha;')
                cursor.execute(TABLEDEF)

    def random_fetch(self, artist, imagetype):
        ''' fetch a random row from a cache for the artist '''
        data = None
        if not self.databasefile.exists():
            logging.error('imagecache db does not exist yet?')
            return None

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            try:
                cursor.execute(
                    '''SELECT * FROM artistsha
 WHERE artist=?
 AND imagetype=?
 AND cachekey NOT NULL
 ORDER BY random() LIMIT 1;''', (
                        artist,
                        imagetype,
                    ))
            except sqlite3.OperationalError as error:
                msg = str(error)
                error_code = error.sqlite_errorcode
                error_name = error.sqlite_name
                logging.debug('Error %s [Errno %s]: %s', msg, error_code,
                              error_name)
                return None

            row = cursor.fetchone()
            if not row:
                return None

            data = {
                'artist': row['artist'],
                'cachekey': row['cachekey'],
                'url': row['url'],
                'strikes': row['strikes']
            }
            logging.debug('random got %s/%s/%s', imagetype, row['artist'],
                          row['cachekey'])

        return data

    def random_image_fetch(self, artist, imagetype):
        ''' fetch a random image from an artist '''
        image = None
        while data := self.random_fetch(artist, imagetype):
            try:
                image = self.cache[data['cachekey']]
            except KeyError as error:
                logging.debug('random: %s', error)
                self.erase_cachekey(data['cachekey'])
            if image:
                self.reset_strikes(data['cachekey'])
                break
        return image

    def find_url(self, url):
        ''' update metadb '''

        data = None
        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return None

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            try:
                cursor.execute('''SELECT * FROM artistsha WHERE url=?''',
                               (url, ))
            except sqlite3.OperationalError as error:
                msg = str(error)
                error_code = error.sqlite_errorcode
                error_name = error.sqlite_name
                logging.debug('Error %s [Errno %s]: %s', msg, error_code,
                              error_name)
                return None

            if row := cursor.fetchone():
                data = {
                    'artist': row['artist'],
                    'cachekey': row['cachekey'],
                    'imagetype': row['imagetype'],
                    'url': row['url'],
                    'strikes': row['strikes'],
                    'timestamp': row['timestamp']
                }
        return data

    def find_cachekey(self, cachekey):
        ''' update metadb '''

        data = None
        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return None

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            try:
                cursor.execute('''SELECT * FROM artistsha WHERE cachekey=?''',
                               (cachekey, ))
            except sqlite3.OperationalError:
                return None

            if row := cursor.fetchone():
                data = {
                    'artist': row['artist'],
                    'cachekey': row['cachekey'],
                    'url': row['url'],
                    'imagetype': row['imagetype'],
                    'strikes': row['strikes'],
                    'timestamp': row['timestamp']
                }

        return data

    def fill_queue(self, config, artist, imagetype, urllist):
        ''' fill the queue '''

        if 'logo' in imagetype:
            maxart = config.cparser.value('artistextras/logos',
                                          defaultValue=3,
                                          type=int)
        elif 'banner' in imagetype:
            maxart = config.cparser.value('artistextras/banners',
                                          defaultValue=3,
                                          type=int)
        elif 'thumb' in imagetype:
            maxart = config.cparser.value('artistextras/thumbnails',
                                          defaultValue=3,
                                          type=int)
        else:
            maxart = config.cparser.value('artistextras/fanart',
                                          defaultValue=20,
                                          type=int)

        logging.debug('Putting %s unfiltered for %s/%s',
                      min(len(urllist), maxart), imagetype, artist)
        for url in random.sample(urllist, min(len(urllist), maxart)):
            self.put_db_url(artist=artist, imagetype=imagetype, url=url)

    def get_next_dlset(self):
        ''' update metadb '''

        def dict_factory(cursor, row):
            d = {}
            for idx, col in enumerate(cursor.description):
                d[col[0]] = row[idx]
            return d

        dataset = None
        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return None

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = dict_factory
            cursor = connection.cursor()
            try:
                cursor.execute(
                    '''SELECT * FROM artistsha WHERE cachekey IS NULL
 AND EXISTS (SELECT * FROM artistsha
 WHERE imagetype='artistthumb' OR imagetype='artistbanner' OR imagetype='artistlogo')
 ORDER BY TIMESTAMP DESC LIMIT 10''')
            except sqlite3.OperationalError as error:
                logging.debug(error)
                return None

            dataset = cursor.fetchall()

            if not dataset:
                try:
                    cursor.execute(
                        '''SELECT * FROM artistsha WHERE cachekey IS NULL
 ORDER BY TIMESTAMP DESC LIMIT 10''')
                except sqlite3.OperationalError as error:
                    logging.debug(error)
                    return None

                dataset = cursor.fetchall()

        return dataset

    def put_db_cachekey(self, artist, url, imagetype, cachekey=None):
        ''' update metadb '''

        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()

            sql = '''
INSERT OR REPLACE INTO
 artistsha(url, artist, cachekey, imagetype, strikes) VALUES(?, ?, ?, ?, 0);
'''
            try:
                cursor.execute(sql, (
                    url,
                    artist,
                    cachekey,
                    imagetype,
                ))
            except sqlite3.OperationalError as error:
                msg = str(error)
                error_code = error.sqlite_errorcode
                error_name = error.sqlite_name
                logging.debug('Error %s [Errno %s]: %s', msg, error_code,
                              error_name)
                return

    def put_db_url(self, artist, url, imagetype=None):
        ''' update metadb '''

        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()

            sql = '''
INSERT INTO
artistsha(url, artist, imagetype, strikes)
VALUES (?,?,?,0);
'''
            try:
                cursor.execute(sql, (
                    url,
                    artist,
                    imagetype,
                ))
            except sqlite3.IntegrityError as error:
                if 'UNIQUE' in str(error):
                    logging.debug('Duplicate URL, ignoring')
                else:
                    logging.debug(error)
            except sqlite3.OperationalError as error:
                logging.debug(error)

    def erase_url(self, url):
        ''' update metadb '''

        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return

        logging.debug('Erasing %s', url)
        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            logging.debug('Delete %s for reasons', url)
            try:
                cursor.execute('DELETE FROM artistsha WHERE url=?;', (url, ))
            except sqlite3.OperationalError:
                return

    def erase_cachekey(self, cachekey):
        ''' update metadb '''

        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return

        data = self.find_cachekey(cachekey)
        if not data:
            return

        if data['strikes'] > 3:
            self.erase_url(data['url'])
            return

        logging.debug('Update %s %s/%s strikes, was %s', data['imagetype'],
                      data['artist'], cachekey, data['strikes'])

        with sqlite3.connect(self.databasefile) as connection:
            connection.row_factory = sqlite3.Row
            cursor = connection.cursor()
            try:
                cursor.execute(
                    'UPDATE artistsha SET strikes=? WHERE cachekey=?;', (
                        data['strikes'] + 1,
                        cachekey,
                    ))
            except sqlite3.OperationalError:
                return

    def reset_strikes(self, cachekey):
        ''' update metadb '''

        if not self.databasefile.exists():
            logging.error('imagecache does not exist yet?')
            return

        data = self.find_cachekey(cachekey)
        if not data:
            logging.debug('Already deleted')
            return

        if data.get('strikes') == 0:
            return

        logging.debug('Resetting strikes on %s %s/%s', data['imagetype'],
                      data['artist'], cachekey)
        with sqlite3.connect(self.databasefile) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    'UPDATE artistsha SET strikes=? WHERE cachekey=?;', (
                        0,
                        cachekey,
                    ))
            except sqlite3.OperationalError:
                return

    def image_dl(self, imagedict):
        ''' fetch an image and store it '''
        nowplaying.bootstrap.setuplogging(logpath=self.logpath)
        threading.current_thread().name = 'ICFollower'
        logging.getLogger('requests_cache').setLevel(logging.CRITICAL + 1)
        logging.getLogger('aiosqlite').setLevel(logging.CRITICAL + 1)
        version = nowplaying.version.get_versions()['version']
        session = requests_cache.CachedSession(self.httpcachefile)
        cachekey = str(uuid.uuid4())

        logging.debug("Downloading %s %s", cachekey, imagedict['url'])
        try:
            headers = {
                'user-agent':
                f'whatsnowplaying/{version}'
                ' +https://whatsnowplaying.github.io/'
            }
            dlimage = session.get(imagedict['url'], timeout=5, headers=headers)
        except Exception as error:  # pylint: disable=broad-except
            logging.debug('image_dl: %s %s', imagedict['url'], error)
            self.erase_url(imagedict['url'])
            return
        if dlimage.status_code == 200:
            image = nowplaying.utils.image2png(dlimage.content)
            self.cache[cachekey] = image
            self.put_db_cachekey(artist=imagedict['artist'],
                                 url=imagedict['url'],
                                 imagetype=imagedict['imagetype'],
                                 cachekey=cachekey)
        else:
            logging.debug('image_dl: status_code %s', dlimage.status_code)
            return

        return

    def queue_process(self, logpath, maxworkers=5):
        ''' Process to download stuff in the background to avoid the GIL '''

        threading.current_thread().name = 'ICQueue'
        nowplaying.bootstrap.setuplogging(logpath=logpath)
        self.logpath = logpath
        self.erase_url('STOPWNP')
        endloop = False
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=maxworkers) as executor:
            while not endloop:
                if dataset := self.get_next_dlset():
                    for stopcheck in dataset:
                        if stopcheck['url'] == 'STOPWNP':
                            endloop = True

                    if endloop:
                        break
                    executor.map(self.image_dl, dataset)
                time.sleep(1)
                if not self.databasefile.exists():
                    logging.error('imagecache db does not exist yet?')
                    break

        logging.debug('stopping download processes')
        self.erase_url('STOPWNP')

    def stop_process(self):
        ''' stop the bg ImageCache process'''
        logging.debug('imagecache stop_process called')
        self.put_db_url('STOPWNP', 'STOPWNP', imagetype='STOPWNP')
        self.cache.close()
        logging.debug('WNP should be set')
