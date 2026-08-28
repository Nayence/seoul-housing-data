terraform {
  backend "s3" {
    bucket       = "seoul-housing-tfstate-anice"
    key          = "seoul-housing/prod/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}