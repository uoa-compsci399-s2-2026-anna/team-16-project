"""Repository-layer failures with stable semantics for the API layer."""


class RepositoryError(RuntimeError):
    pass


class NoPublishedFactorSetError(RepositoryError):
    pass


class FactorSetNotFoundError(RepositoryError):
    pass


class FactorSetStateError(RepositoryError):
    pass


class TaxonomyInvariantError(RepositoryError):
    pass

