from __future__ import absolute_import
import gutenberg.beautify as beautify
import gutenberg.common.configutil as configutil
import gutenberg.common.functutil as functutil
import gutenberg.common.osutil as osutil
import gutenberg.download as download
import gutenberg.metainfo as metainfo
import itertools
import json
import logging
import os
import sqlalchemy
import sqlalchemy.ext.declarative
import sqlalchemy.orm


Base = sqlalchemy.ext.declarative.declarative_base()


class GutenbergCorpus(object):
    def __init__(self):
        BASEDIR = 'ProjectGutenbergCorpus'
        self.cfg = configutil.ConfigMapping()
        self.cfg.download = configutil.ConfigMapping.Section()
        self.cfg.download.data_path = os.path.join(BASEDIR, 'rawdata')
        self.cfg.download.offset = 0
        self.cfg.metadata = configutil.ConfigMapping.Section()
        self.cfg.metadata.metadata = os.path.join(BASEDIR, 'metadata.json.gz')
        self.cfg.database = configutil.ConfigMapping.Section()
        self.cfg.database.drivername = 'sqlite'
        self.cfg.database.username = None
        self.cfg.database.password = None
        self.cfg.database.host = None
        self.cfg.database.port = None
        self.cfg.database.database = os.path.join(BASEDIR, 'gutenberg.db3')

    @classmethod
    def using_config(cls, config_path):
        corpus = GutenbergCorpus()
        corpus.cfg.merge(configutil.ConfigMapping.from_config(config_path))
        return corpus

    def write_config(self, path):
        self.cfg.write_config(path)

    @functutil.memoize
    def etext_metadata(self):
        opener = osutil.opener(self.cfg.metadata.metadata)
        try:
            with opener(self.cfg.metadata.metadata, 'rb') as metadata_file:
                json_items = json.load(metadata_file).iteritems()
                metadata = dict((int(key), val) for (key, val) in json_items)
        except IOError:
            metadata = metainfo.metainfo()
            osutil.makedirs(os.path.dirname(self.cfg.metadata.metadata))
            with opener(self.cfg.metadata.metadata, 'wb') as metadata_file:
                json.dump(metadata, metadata_file, sort_keys=True, indent=2)
        return metadata

    def download(self, filetypes='txt', langs='en'):
        osutil.makedirs(self.cfg.download.data_path)
        self.cfg.download.offset = download.download_corpus(
            self.cfg.download.data_path, filetypes=filetypes, langs=langs,
            offset=int(self.cfg.download.offset))

    def _dbsession(self):
        osutil.makedirs(os.path.dirname(self.cfg.database.database))
        engine = sqlalchemy.create_engine(sqlalchemy.engine.url.URL(
            drivername=self.cfg.database.drivername,
            username=self.cfg.database.username,
            password=self.cfg.database.password,
            host=self.cfg.database.host,
            port=self.cfg.database.port,
            database=osutil.canonical(self.cfg.database.database),
        ))
        Base.metadata.create_all(engine)
        Session = sqlalchemy.orm.sessionmaker(bind=engine)
        return Session()

    def persist(self):
        session = self._dbsession()
        existing = set(etext.etextno for etext in session.query(EText).all())
        files, num_added = osutil.listfiles(self.cfg.download.data_path), 0
        for path in files:
            logging.debug('processing %s', path)
            try:
                etext = EText.from_file(path, self.etext_metadata())
            except Exception as ex:  # pylint: disable=W0703
                logging.error('skipping %s: [%s] %s',
                              path, type(ex).__name__, ex.message)
                continue
            if etext.etextno not in existing:
                session.add(etext)
                existing.add(etext.etextno)
                num_added += 1
                if num_added % 100 == 0:
                    logging.debug('committing')
                    session.commit()
        session.commit()


class EText(Base):
    __tablename__ = 'etexts'

    etextno = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
    author = sqlalchemy.Column(sqlalchemy.Unicode)
    title = sqlalchemy.Column(sqlalchemy.Unicode)
    fulltext = sqlalchemy.Column(sqlalchemy.UnicodeText)

    @classmethod
    def from_file(cls, fobj, etext_metadata):
        lines = fobj if isinstance(fobj, file) else osutil.readfile(fobj)
        lines = (unicode(line, 'latin1') for line in lines)
        metaiter, fulltextiter = itertools.tee(lines, 2)
        ident = metainfo.etextno(metaiter)
        text = u'\n'.join(beautify.strip_headers(fulltextiter))
        metadata = etext_metadata[ident]
        author = metadata.get('author')
        title = metadata.get('title')
        if author is None:
            logging.warning('no author available for etext %s', ident)
        if title is None:
            logging.warning('no title available for etext %s', ident)
        return EText(etextno=ident, author=author, title=title, fulltext=text)

    def __repr__(self):
        return ('{clsname}(author="{author}", title="{title}", text="{text}")'
                .format(
                    clsname=self.__class__.__name__,
                    author=self.author,
                    title=self.title,
                    text=self.fulltext[:15] + '...'))
