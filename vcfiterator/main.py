import sys
from collections import defaultdict
import re

from vcfiterator.processors import NativeInfoProcessor, CsvAlleleParser
from vcfiterator.util import Util

# Official fields in specification
SPEC_FIELDS = [
    'CHROM',
    'POS',
    'ID',
    'REF',
    'ALT',
    'QUAL',
    'FILTER',
    'INFO',
    'FORMAT'
]


class HeaderParser(object):
    """
    Class for parsing the header part of the vcf and returns the metadata and header data.
    """

    RE_INFO = re.compile(r'[<]*(.*?)=["]*(.*?)["]*[,>]')

    def __init__(self, path):
        self.path = path
        self.metaProccessors = {
            'INFO': self._parseMetaInfo,
            'FILTER': self._parseMetaInfo,
            'FORMAT': self._parseMetaInfo
        }

    def _getSamples(self, header):
        return [field for field in header if field not in SPEC_FIELDS]

    def _parseMetaInfo(self, infoline):
        groups = re.findall(HeaderParser.RE_INFO, infoline)
        info = {k: v for k, v in groups}
        return info

    def _parseHeader(self):
        meta = defaultdict(list)
        header = list()

        # Read in metadata and header
        with open(self.path) as fd:
            for line in fd.xreadlines():
                line = line.replace('\n', '')
                if line.startswith('##'):
                    key, value = line[2:].split('=', 1)
                    meta[key].append(value)
                elif(line.startswith('#')):
                    line = line.replace('#', '')
                    header = line.split('\t')
                else:
                    # End of header
                    break

        # Extract data with processors
        for key, func in self.metaProccessors.iteritems():
            if key in meta:
                for idx, value in enumerate(meta[key]):
                    meta[key][idx] = func(value)

        # Extract value from single-item lists ([val] -> val):
        for k, v in meta.iteritems():
            if len(v) == 1:
                meta[k] = v[0]

        samples = self._getSamples(header)
        return meta, header, samples

    def parse(self):
        return self._parseHeader()


class DataParser(object):

    def __init__(self, path, meta, header, samples):
        self.path = path
        self.meta = meta
        self.header = header
        self.samples = samples

        self.infoProcessors = list()
        self.fallbackProcessor = NativeInfoProcessor(meta)

    def addInfoProcessor(self, processor):
        self.infoProcessors.append(processor)

    def _parseDataInfoField(self, data):
        """
        Parses the INFO data into data structures.
        Data is split into general ('ALL') and allele specific data.
        """

        alleles = data['ALT']

        fields = data['INFO'].split(';')

        # Create dict for allele specific INFO
        info_data = {
            k: dict() for k in alleles
        }
        # And include INFO for 'ALL' alleles
        info_data['ALL'] = dict()

        for f in fields:
            if '=' in f:
                key, value = f.split('=', 1)
            else:
                key, value = f, True
            # Process keys by processor, if present, or use native processor
            # Data is inserted into info_data by the functions
            processed = False
            for processor in self.infoProcessors:
                if processor.accepts(key, value, processed):
                    processor.process(key, value, info_data, alleles, processed)
                    processed = True
            # If no processors handled the data, use the native header processor
            if not processed:
                self.fallbackProcessor.process(key, value, info_data, alleles)

        data['INFO'] = info_data

    def _parseDataSampleFields(self, data):
        sample_format = data['FORMAT'].split(':')

        samples = dict()
        extract = Util.split_and_convert(Util.conv_to_number, extract_single=True)
        for sample_name in self.samples:
            sample_text = data.pop(sample_name)
            samples[sample_name] = {
                k: extract(v) for k, v in zip(sample_format, sample_text.split(':'))
            }

        data['SAMPLES'] = samples

        del data['FORMAT']

    def _parseData(self, line):
        data = {
            k: v for k, v in zip(self.header, line.split('\t'))
        }

        # Split by alleles
        data['ALT'] = data['ALT'].split(',')

        self._parseDataInfoField(data)

        self._parseDataSampleFields(data)

        # Manual conversion
        data['POS'] = Util.conv_to_number(data['POS'])
        data['QUAL'] = Util.conv_to_number(data['QUAL'])

        return data

    def iter(self, throw_exceptions=True):
        found_data_start = False
        with open(self.path) as fd:
            for line_idx, line in enumerate(fd.xreadlines()):
                # Skip header, wait for #CHROM to signal start of data
                if line.startswith('#CHROM') and not found_data_start:
                    found_data_start = True
                    continue
                if not found_data_start:
                    continue
                line = line.replace('\n', '')
                try:
                    data = self._parseData(line)
                except Exception:
                    if throw_exceptions:
                        raise
                    else:
                        sys.stderr.write("WARNING: Line {} failed to parse: \n {}".format(line_idx, line))

                yield data


class VcfIterator(object):

    def __init__(self, path):
        self.path = path
        self.meta, self.header, self.samples = HeaderParser(self.path).parse()
        self.data_parser = DataParser(self.path, self.meta, self.header, self.samples)

        # Add by default
        self.addInfoProcessor(CsvAlleleParser)

    def getHeader(self):
        return self.header

    def getMeta(self):
        return self.meta

    def getSamples(self):
        return self.samples

    def addInfoProcessor(self, processor):
        self.data_parser.addInfoProcessor(processor(self.meta))

    def iter(self, throw_exceptions=True):
        for r in self.data_parser.iter(throw_exceptions=throw_exceptions):
            yield r


if __name__ == '__main__':
    import json

    path = sys.argv[1]
    v = VcfIterator(path)

    for value in v.iter():
        print json.dumps(value, indent=4)