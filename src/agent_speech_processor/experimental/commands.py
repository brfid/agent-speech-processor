"""Bootstrap explicitly selected experimental commands, without loading weights."""


def dispatch(args, config):
    from .models import install, MODEL_ROOT, identity, runtime_versions
    if args.experiment == "install-model":
        return install(args.name, args.root or MODEL_ROOT), 0
    if args.experiment == "review":
        args.session = args.session or config.get("active_session")
        if not args.session:
            raise ValueError("Supply --session or configure active_session in the local workspace file")
    from .review import recipe
    from .audio import windows
    list(windows(1, args.core_seconds, args.context_seconds))
    spec = recipe(identity(args.reviewer_model), identity(args.diarizer_model), runtime_versions(),
                  args.core_seconds, args.context_seconds, args.reviewer)
    if args.experiment == "evaluate":
        from .evaluate import evaluate
        result = evaluate(args, spec)
        return result, 0 if result["passed"] else 2
    from .pipeline import process
    result = process(args, spec)
    return result, 0 if result["complete"] else 2
