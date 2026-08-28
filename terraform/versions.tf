# Verrouillage des versions.
#
# Sans contrainte de version, un "terraform init" execute dans six mois
# telechargerait une version plus recente du provider, avec potentiellement
# un comportement different. La reproductibilite passe par la.
#
# ~> 6.0 accepte 6.1, 6.2... mais refuse 7.0 (changements incompatibles).

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
