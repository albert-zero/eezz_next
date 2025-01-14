# -*- coding: utf-8 -*-
"""
    This module implements the following classes:

    * **TFile**:        Takes a chunk of data and merges it to a file
    * **TEezzFile**:    Extends TFile and implements encryption and decryption for transmitted data
    * **TFileMode**:    Enum file-mode for TEezzFiles

    This module supports a download of big files in chunks and ensures, that the incoming fragments are
    put together in the correct order again. Furthermore, a hash is calculated for each chunk, so that the
    data consistency of a file could be ensured during reading.
"""
from    loguru          import logger
import  os
import  mmap
from    typing         import Any, List

from    Crypto.Hash    import SHA256
from    Crypto.Cipher  import AES
from    Crypto         import Random

from    dataclasses    import dataclass
from    pathlib        import Path
from    service        import TService
from    io             import BufferedReader
from    enum           import Enum


class TFileMode(Enum):
    """ File mode: Determine how to handle incoming stream

    :param NORMAL:      Write through
    :param ENCRYPT:     Encrypt and write
    :param ENCRYPT:     Decrypt and write
    """
    NORMAL  = 0         #: :meta private:
    ENCRYPT = 1         #: :meta private:
    DECRYPT = 2         #: :meta private:


@dataclass(kw_only=True)
class TFile:
    """ Class to be used as file download handler. It accepts chunks of data in separate calls

    :param file_type:   User defined file type
    :param destination: Path to store the file
    :param size:        The size of the file
    :param chunk_size:  Fixed size for each chunk of data, except the last element
    """
    file_type:   str        #: :meta private:
    destination: Path       #: :meta private:
    size:        int        #: :meta private:
    chunk_size:  int        #: :meta private:
    transferred: int = 0    #: :meta private:

    def __post_init__(self):
        self.chunk_count = divmod(self.size, self.chunk_size)[0] + 1
        self.hash_chain  = ['' for x in range(self.chunk_count)]
        self.transferred = 0

        with self.destination.open('w+b') as x_output:
            x_output.seek(self.size-1)
            x_output.write(b'\x00')

    @property
    def name(self) -> str:
        """ :return: Returns the file path """
        return self.destination.name

    def write(self, raw_data: Any, sequence_nr: int, mode: TFileMode = TFileMode.NORMAL) -> str:
        """ Write constant chunks of raw data to file. Only the last chunk might be smaller.
        The sequence number is passed along, because we cannot guarantee, that elements received in the same
        order as they are send.

        :param Any          raw_data:       Raw chunk of data
        :param int          sequence_nr:    Sequence number to insert chunks at the right place
        :param TFileMode    mode:           Set signature for derived classes
        :return: Signature for derived classes
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
        return ''


@dataclass(kw_only=True)
class TEezzFile(TFile):
    """ Derived from TFile, this class allows encryption and decryption using AES key.
    After finishing the transfer, the instance is pushed into the response queue, which allows to implement a
    supervisor thread, which blocks on the queue reading

    :param Crypto.Random.new(16)  key:          AES key for cypher
    :param Crypto.Random.new(16)  vector:       AES vector for cypher
    :param List[SHA256.hexdigest] hash_chain:   A list of hash values for each chunk
    """
    key:          bytes         #: :meta private:
    vector:       bytes         #: :meta private:
    hash_chain:   dict  = None  #: :meta private:

    def __post_init__(self):
        super().__post_init__()
        self.cypher     = AES.new(self.key, AES.MODE_CBC, self.vector)
        self.hash_chain = dict()

    def write(self, raw_data: Any, sequence_nr: int, mode: TFileMode = TFileMode.ENCRYPT) -> str:
        """ Write a chunk of data

        :param raw_data:    The data chunk to write
        :param sequence_nr: Sequence of the data chunk in the stream
        :param mode:        The mode used to en- or decrypt the data or pass through
        :return: The hash value of the data after encryption/before decryption
        :rtype:  SHA256.hexdigest
        """
        if mode == TFileMode.ENCRYPT:
            x_hash = self.encrypt(raw_data=raw_data, sequence_nr=sequence_nr)
        elif mode == TFileMode.DECRYPT:
            x_hash = self.decrypt(raw_data=raw_data, sequence_nr=sequence_nr)
        else:
            super().write(raw_data, sequence_nr)
            x_hash = SHA256.new(raw_data).hexdigest()
        return x_hash

    def read(self, source: BufferedReader, hash_list: List[str] = None) -> None:
        """ Read an encrypted file from source input stream and create an decrypted version

        :param BufferedReader           source:     Input stream
        :param List[SHA256.hexdigest]   hash_list:  A hash list to check the stream
        """
        x_sequence_nr = 0
        while True:
            x_raw_data = source.read(self.chunk_size)
            x_hash     = self.write(x_raw_data, x_sequence_nr, TFileMode.DECRYPT)
            # todo check x_hash and hash_list[x_sequence_nr] raise FileCypherError
            if x_hash != hash_list[x_sequence_nr]:
                pass
            if self.transferred >= self.size:
                break
            x_sequence_nr += 1

    def encrypt(self, raw_data: Any, sequence_nr: int) -> str:
        """ Encrypt the incoming data stream

        :param Any raw_data:    Data chunk of the stream
        :param int sequence_nr: Sequence number in the stream
        :return: Hash value of the chunk
        :rtype:  SHA256.hexdigest
        """
        x_stream = raw_data
        # for encryption all chunks have to be extended to 16-byte mod length
        if self.file_type == 'main':
            x_dimension  = divmod(len(raw_data), 16)
            if x_dimension[1] > 0:
                x_stream   += (16 - x_dimension[1]) * b'\x00'
            x_stream = self.cypher.encrypt(bytes(raw_data))

        x_hash = SHA256.new(x_stream).hexdigest()
        self.hash_chain[sequence_nr] = x_hash
        super().write(x_stream, sequence_nr)
        return x_hash

    def decrypt(self, raw_data: Any, sequence_nr: int) -> str:
        """ Decrypt the incoming stream

        :param Any raw_data:    Data chunk of the steam
        :param int sequence_nr: Sequence number in the stream
        :return: Hash value of the chunk
        :rtype:  SHA256.hexdigest
        """
        x_stream = raw_data
        x_hash   = SHA256.new(raw_data).hexdigest()
        self.hash_chain[sequence_nr] = x_hash

        if self.file_type == 'main':
            x_stream = self.cypher.decrypt(bytes(raw_data))
        super().write(x_stream, sequence_nr)
        return x_hash


# --- Section for module tests
def test_file_reader():
    """ Test the TFile interfaces
    :meta private:
    """
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
    TEezzFile.read does the decryption using key and vector variables

    :meta private:
    """
    x_source    = Path(TService().root_path) / 'testdata/bird.jpg'
    x_stat      = os.stat(x_source)
    x_size      = x_stat.st_size
    x_dest      = TService().document_path / x_source.with_suffix('.crypt').name
    x_decr      = TService().document_path / x_source.name
    x_key       = Random.new().read(AES.block_size)
    x_vector    = Random.new().read(AES.block_size)

    try:
        x_dest.unlink()
    except FileNotFoundError:
        pass

    logger.debug(f'--- Test TEezzFile: {x_source} --> {x_dest}, file-type=main')
    x_file = TEezzFile(destination=x_dest, size=x_size, chunk_size=1024, file_type='main', key=x_key, vector=x_vector)
    with x_source.open('rb+') as x_input:
        x_sequence = 0
        while True:
            x_chunk = x_input.read(1024)
            if len(x_chunk) == 0:
                break
            x_file.write(raw_data=x_chunk, sequence_nr=x_sequence)
            x_sequence += 1
    logger.debug(f'hash-list = {x_file.hash_chain}')

    logger.debug(f'--- Test TEezzFile: {x_dest} --> {x_decr}, file-type=main')
    x_file = TEezzFile(destination=x_decr, size=x_size, chunk_size=1024, file_type='main', key=x_key, vector=x_vector)
    with x_dest.open('rb+') as x_input:
        x_sequence = 0
        while x_chunk := x_input.read(1024):
            x_file.write(raw_data=x_chunk, sequence_nr=x_sequence, mode=TFileMode.DECRYPT)
            x_sequence += 1
    logger.debug(f'hash-list = {x_file.hash_chain}')
    logger.success('test TFile')


if __name__ == '__main__':
    """ :meta private:  """
    x_service  = TService.set_environment(root_path=r'C:\Users\alzer\Projects\github\eezz_full\webroot')
    x_log_path = x_service.logging_path / 'app.log'

    test_file_reader()
    test_eezzfile_reader()
