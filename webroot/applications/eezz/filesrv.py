# -*- coding: utf-8 -*-
"""
    EezzServer:
    High speed application development and
    high speed execution based on HTML5

    Copyright (C) 2023  Albert Zedlitz

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import logging
import os
import mmap
import base64
import io
import json
import time

from    typing         import Any, Callable, Dict, List
from    Crypto.Hash    import SHA256
from    Crypto.Cipher  import AES
from    Crypto         import Random
from    dataclasses    import dataclass
from    threading      import Condition, Thread
from    pathlib        import Path
from    service        import TService
from    queue          import Queue
import  tarfile
import  mimetypes
from    io             import BufferedReader


@dataclass(kw_only=True)
class TFile:
    """ Class to be used as handle for file download """
    file_type:   str
    destination: Path
    size:        int
    chunk_size:  int

    def __post_init__(self):
        self.chunk_count = divmod(self.size, self.chunk_size)[0] + 1
        self.hash_chain  = ['' for x in range(self.chunk_count)]
        self.transferred = 0

        with self.destination.open('w+b') as x_output:
            x_output.seek(self.size-1)
            x_output.write(b'\x00')

    def write(self, raw_data: Any, sequence_nr: int):
        """ Write constant chunks of raw data to file. The last chunk might be smaller
        :param raw_data: Raw chunk of data
        :param sequence_nr: Sequence number, counting the number of chunks and could re-arrange segments
        """
        x_offset          = sequence_nr * self.chunk_size
        self.transferred += len(raw_data)
        if self.transferred > self.size:
            raw_data = raw_data[:self.size - self.transferred]

        with self.destination.open("r+b") as x_out:
            # Accept any chunk any time
            x_data_slice      = raw_data[:]
            x_memory_map      = mmap.mmap(x_out.fileno(), 0)
            x_memory_view     = memoryview(x_memory_map)
            x_memory_slice    = x_memory_view[x_offset: x_offset + len(raw_data)]
            x_memory_slice[:] = x_data_slice

            x_memory_slice.release()
            x_memory_view.release()
            x_memory_map.close()


@dataclass(kw_only=True)
class TEezzFile(TFile):
    key:          bytes
    vector:       bytes
    response:     Queue = None
    hash_chain:   dict  = None

    def __post_init__(self):
        # super().__init__(destination=self.destination, size=self.size, chunk_size=self.chunk_size)
        super().__post_init__()
        self.cypher     = AES.new(self.key, AES.MODE_CBC, self.vector)
        self.hash_chain = dict()

    def write(self, raw_data: Any, sequence_nr: int):
        self.encrypt(raw_data=raw_data, sequence_nr=sequence_nr)
        if self.transferred >= self.size and self.response:
            self.response.put(self)

    def readio(self, source: BufferedReader) -> None:
        x_sequence_nr = 0
        while True:
            x_raw_data = source.read(self.chunk_size)
            x_hash     = self.read(x_raw_data, x_sequence_nr)
            if self.transferred >= self.size:
                break
            x_sequence_nr += 1

    def read(self, raw_data: Any, sequence_nr: int) -> bytes:
        """ Decrypt chunk of data
        :param raw_data: Encrypted data
        :param sequence_nr: Sequence number in file
        :return: Hash value of raw_data, which could be used to verify the data before usage
        """
        x_hash = self.decrypt(raw_data=raw_data, sequence_nr=sequence_nr)
        if self.transferred >= self.size and self.response:
            self.response.put(self.destination)
        return x_hash

    def encrypt(self, raw_data: Any, sequence_nr: int):
        x_stream = raw_data
        # for encryption all chunks have to be extended to 16-byte mod length
        if self.file_type == 'main':
            x_dimension  = divmod(len(raw_data), 16)
            if x_dimension[1] > 0:
                x_stream   += (16 - x_dimension[1]) * b'\x00'
            x_stream = self.cypher.encrypt(bytes(raw_data))

        x_hash = SHA256.new()
        x_hash.update(x_stream)
        self.hash_chain[sequence_nr] = base64.b64encode(x_hash.digest()).decode('utf-8')
        super().write(x_stream, sequence_nr)

    def decrypt(self, raw_data: Any, sequence_nr: int) -> bytes:
        x_stream = raw_data
        x_hash   = SHA256.new()
        x_hash.update(raw_data)
        self.hash_chain[sequence_nr] = base64.b64encode(x_hash.digest()).decode('utf-8')

        if self.file_type == 'main':
            x_stream = self.cypher.decrypt(bytes(raw_data))
        super().write(x_stream, sequence_nr)
        return self.hash_chain[sequence_nr]


# --- Section for module tests
def test_file_reader():
    """ Test the TFile interfaces """
    # Read a file and test the TFile downloader
    x_source = Path(TService().root_path) / 'testdata/bird.jpg'
    x_stat   = os.stat(x_source)
    x_size   = x_stat.st_size

    x_dest   = TService().document_path / x_source.with_suffix('.eezz').name
    try:
        x_dest.unlink()
    except FileNotFoundError:
        pass

    print(f'{x_source} --> {x_dest}')
    x_file      = TFile(destination=x_dest, size=x_size, chunk_size=1024, file_type='doc')
    with x_source.open('rb+') as x_input:
        x_sequence = 0
        while True:
            x_chunk = x_input.read(1024)
            if len(x_chunk) == 0:
                break
            x_file.write(raw_data=x_chunk, sequence_nr=x_sequence)
            x_sequence += 1


def test_eezzfile_reader():
    """ Test the TEezzFile(TFile) interfaces
    TEezzFile.write would encrypt the file while
    TEezzFile.read does the decryption using key and vector variables """
    x_source    = Path(TService().root_path) / 'testdata/bird.jpg'
    x_stat      = os.stat(x_source)
    x_size      = x_stat.st_size
    x_queue     = Queue()
    x_dest      = TService().document_path / x_source.with_suffix('.crypt').name
    x_decr      = TService().document_path / x_source.name
    x_key       = Random.new().read(AES.block_size)
    x_vector    = Random.new().read(AES.block_size)

    try:
        x_dest.unlink()
    except FileNotFoundError:
        pass

    print(f'{x_source} --> {x_dest}')
    x_file = TEezzFile(destination=x_dest, size=x_size, chunk_size=1024,
                       file_type='main', response=x_queue, key=x_key, vector=x_vector)
    with x_source.open('rb+') as x_input:
        x_sequence = 0
        while True:
            x_chunk = x_input.read(1024)
            if len(x_chunk) == 0:
                break
            x_file.write(raw_data=x_chunk, sequence_nr=x_sequence)
            x_sequence += 1
    print(x_file.hash_chain)

    x_file = TEezzFile(destination=x_decr, size=x_size, chunk_size=1024,
                       file_type='main', response=x_queue, key=x_key, vector=x_vector)
    with x_dest.open('rb+') as x_input:
        x_sequence = 0
        while True:
            x_chunk = x_input.read(1024)
            if len(x_chunk) == 0:
                break
            x_file.read(raw_data=x_chunk, sequence_nr=x_sequence)
            x_sequence += 1
    print(x_file.hash_chain)


if __name__ == '__main__':
    test_file_reader()
    test_eezzfile_reader()
