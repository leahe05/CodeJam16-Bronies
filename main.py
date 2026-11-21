"""LookAt entry point. For now: prints the skeleton check and the config."""
from lookat import config


def main() -> None:
    print("LookAt skeleton OK")
    for name in sorted(dir(config)):
        if name.isupper():
            print(f"  {name} = {getattr(config, name)!r}")


if __name__ == "__main__":
    main()
