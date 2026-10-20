"""An immutable view of existing ActorManifest exports, never an actor catalog."""
from .contracts import digest, read_documents


class Registry:
    def __init__(self, documents):
        self.actors = {}
        for doc in documents.values():
            if doc['dtype'] != 'actor-manifest':
                raise ValueError('registry export must contain ActorManifest documents')
            actor = doc['actor']
            if not actor or actor in self.actors:
                raise ValueError('empty or duplicate actor identity')
            self.actors[actor] = {'actor': actor, 'accepts': doc.get('accepts', []),
                'produces': doc.get('produces', []), 'manifestId': doc['id'],
                'manifestDigest': digest(doc), 'capabilities': doc.get('capabilities', []),
                'runtime': doc.get('runtime'), 'targetOptions': doc.get('targetOptions', {})}
        self.identity = digest([self.actors[k] for k in sorted(self.actors)])

    @classmethod
    def load(cls, path, canonical):
        return cls(read_documents(path, canonical))
