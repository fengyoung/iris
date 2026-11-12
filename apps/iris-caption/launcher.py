"""PyInstaller launcher: preserve multiprocessing frozen-worker arguments."""
import multiprocessing

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from iris_caption.app import main
    raise SystemExit(main())
