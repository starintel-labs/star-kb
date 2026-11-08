% Durable architecture facts; tests are recorded separately in run evidence.
learning_contract('star.kb.learning/1').
canonical_authority(starintel_json).
learning_schema('0.10.1', hash_pinned_starlang_generated_schema).
learning_store(sqlite, replay_cache_and_typed_journal).
learning_scope(project_operation_dataset).
learning_invention(bounded_field_equivalence, operational_definition).
learning_evaluation(source_disjoint_labelled_pairs, finite_sample_only).
learning_collection(replay_adapter, not_live_dispatch).
learning_invariant(missing_observation, unresolved_not_negative).
learning_invariant(source_text, evidence_not_instructions).
learning_invariant(accepted_definition, heldout_evidence_dependencies).
