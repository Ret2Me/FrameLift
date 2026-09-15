mod cli;

fn main() {
    match cli::run() {
        Ok(value) => {
            let failed = cli::io::report_failed(&value);
            if let Err(error) = cli::io::print_result(&value) {
                eprintln!("{error}");
                std::process::exit(1);
            }
            if failed {
                std::process::exit(2);
            }
        }
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
